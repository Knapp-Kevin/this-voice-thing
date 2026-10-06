"""Qt microphone capture for Live Voice.

Capture stays separate from routing. QAudioSource feeds a bounded ring buffer;
a worker thread normalizes/processes PCM and emits the shared AudioFrame type.
"""

import threading
import time

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource

from this_voice_thing.core.microphone_audio import (
    MicrophoneBlockProcessor,
    MicrophoneEffectsConfig,
    PcmRingBuffer,
)


class MicrophoneProcessingThread(QThread):
    frame_ready = Signal(object)
    failed = Signal(str)
    complete = Signal(object)

    def __init__(self, ring, *, sample_rate, channels, effects=None, parent=None):
        super().__init__(parent)
        self.ring = ring
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.processor = MicrophoneBlockProcessor(effects)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        self.ring.close(discard=True)

    def run(self):
        try:
            while not self._stop.is_set():
                raw = self.ring.pop(timeout=0.05)
                if raw is None:
                    if self.ring.closed:
                        break
                    continue
                frame = self.processor.process(
                    raw,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                )
                if frame is not None and not self._stop.is_set():
                    self.frame_ready.emit(frame)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            metrics = self.processor.metrics()
            metrics["ring"] = self.ring.stats()
            metrics["cancelled"] = bool(self._stop.is_set())
            self.complete.emit(metrics)


class LiveAudioInput(QObject):
    """Low-level QAudioSource capture with bounded worker-side processing."""

    frame_ready = Signal(object)
    failed = Signal(str)
    started = Signal(object)
    stopped = Signal(object)

    RING_BUFFER_SECONDS = 0.25
    SOURCE_BUFFER_SECONDS = 0.05

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._io = None
        self._device = None
        self._format = None
        self._effects = MicrophoneEffectsConfig()
        self._ring = None
        self._worker = None
        self._active = False
        self._started_at = None
        self._captured_bytes = 0
        self._last_metrics = {}

    @staticmethod
    def device_key(device):
        try:
            return bytes(device.id())
        except Exception:
            return str(device.description()).encode("utf-8", "replace")

    @staticmethod
    def _format(rate, channels):
        fmt = QAudioFormat()
        fmt.setSampleRate(int(rate))
        fmt.setChannelCount(int(channels))
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return fmt

    def _choose_format(self, device):
        preferred = device.preferredFormat()
        rates = []
        for rate in (48000, preferred.sampleRate(), 44100, 16000):
            rate = int(rate or 0)
            if rate > 0 and rate not in rates:
                rates.append(rate)

        channels = []
        for count in (1, preferred.channelCount(), 2):
            count = int(count or 0)
            if count in (1, 2) and count not in channels:
                channels.append(count)

        for rate in rates:
            for count in channels:
                fmt = self._format(rate, count)
                if device.isFormatSupported(fmt):
                    return fmt

        raise RuntimeError(
            f"{device.description()} does not advertise a compatible mono/stereo Int16 input format."
        )

    def configure(self, device, effects=None):
        self.stop()
        if device is None:
            raise RuntimeError("Choose a microphone/input device first.")
        self._device = device
        self._format = self._choose_format(device)
        self._effects = (effects or MicrophoneEffectsConfig()).normalized()
        return {
            "sample_rate": int(self._format.sampleRate()),
            "channels": int(self._format.channelCount()),
            "device": device.description(),
            "provenance": (
                "microphone-passthrough"
                if self._effects.is_neutral()
                else "microphone-dsp"
            ),
        }

    def start(self):
        if self._device is None or self._format is None:
            raise RuntimeError("Configure a microphone before starting capture.")
        if self._active:
            return

        rate = int(self._format.sampleRate())
        channels = int(self._format.channelCount())
        bytes_per_second = rate * channels * 2
        self._ring = PcmRingBuffer(
            max(4096, int(bytes_per_second * self.RING_BUFFER_SECONDS))
        )
        self._worker = MicrophoneProcessingThread(
            self._ring,
            sample_rate=rate,
            channels=channels,
            effects=self._effects,
            parent=self,
        )
        self._worker.frame_ready.connect(self.frame_ready.emit)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.complete.connect(self._on_worker_complete)
        self._worker.start()

        self._source = QAudioSource(self._device, self._format, self)
        self._source.setBufferSize(
            max(4096, int(bytes_per_second * self.SOURCE_BUFFER_SECONDS))
        )
        self._source.stateChanged.connect(self._on_state_changed)

        self._active = True
        self._started_at = time.monotonic()
        self._captured_bytes = 0
        self._last_metrics = {}
        self._io = self._source.start()
        if self._io is None:
            worker = self._worker
            self._worker = None
            self._active = False
            if worker is not None:
                worker.stop()
                worker.wait(250)
            try:
                self._source.stop()
            except Exception:
                pass
            self._source.deleteLater()
            self._source = None
            self._ring.close(discard=True)
            raise RuntimeError("Qt could not open the selected microphone/input device.")
        self._io.readyRead.connect(self._read_available)
        self.started.emit(self.stats())

    def _read_available(self):
        if not self._active or self._io is None or self._ring is None:
            return
        try:
            data = bytes(self._io.readAll())
            if not data:
                return
            self._captured_bytes += len(data)
            self._ring.push(data)
        except Exception as exc:
            self.failed.emit(f"Microphone capture failed: {exc}")
            self.stop()

    def _on_state_changed(self, state):
        if not self._active or self._source is None:
            return
        if state != QAudio.State.StoppedState:
            return
        try:
            error = self._source.error()
        except Exception:
            error = None
        if error not in (None, QAudio.Error.NoError):
            self.failed.emit(
                f"Microphone input stopped unexpectedly ({error})."
            )
            self.stop()

    def _on_worker_failed(self, message):
        self.failed.emit(str(message))
        self.stop()

    def _on_worker_complete(self, metrics):
        merged = self.stats()
        merged["processing"] = dict(metrics or {})
        self._last_metrics = merged
        self.stopped.emit(dict(merged))

    def is_active(self):
        return bool(self._active)

    def stats(self):
        ring = self._ring.stats() if self._ring is not None else {}
        return {
            "active": bool(self._active),
            "input_device": (
                self._device.description() if self._device is not None else None
            ),
            "sample_rate": (
                int(self._format.sampleRate()) if self._format is not None else None
            ),
            "channels": (
                int(self._format.channelCount()) if self._format is not None else None
            ),
            "captured_bytes": int(self._captured_bytes),
            "active_seconds": (
                round(time.monotonic() - self._started_at, 4)
                if self._started_at is not None
                else None
            ),
            "ring": ring,
            "effects": self._effects.as_dict(),
        }

    def last_stats(self):
        return dict(self._last_metrics or self.stats())

    def stop(self):
        was_active = self._active
        self._active = False

        io = self._io
        self._io = None
        if io is not None:
            try:
                io.readyRead.disconnect(self._read_available)
            except Exception:
                pass

        if self._source is not None:
            try:
                self._source.stop()
            except Exception:
                pass
            self._source.deleteLater()
        self._source = None

        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop()
            worker.wait(250)

        if self._ring is not None:
            self._ring.close(discard=True)

        if was_active and not self._last_metrics:
            self._last_metrics = self.stats()
