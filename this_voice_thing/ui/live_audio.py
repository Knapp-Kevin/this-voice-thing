"""Qt runtime for Live Voice generation and raw PCM playback."""

import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink


class LiveSpeechThread(QThread):
    stream_started = Signal(object)
    frame_ready = Signal(object)
    session_complete = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            self.stream_started.emit({
                "mode": self.session.delivery_mode,
                "sample_rate": self.session.sample_rate,
                "provenance": self.session.provenance,
            })
            for frame in self.session.frames(self._stop.is_set):
                if self._stop.is_set():
                    break
                self.frame_ready.emit(frame)
            self.session_complete.emit(self.session.metrics())
        except Exception as exc:
            self.error_occurred.emit(f"{type(exc).__name__}: {exc}")


class StreamingPcmConverter:
    """Stateful mono s16le rate/channel conversion for a continuous stream."""

    def __init__(self, source_rate, target_rate, target_channels):
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        self.target_channels = int(target_channels)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._position = 0.0
        self._step = self.source_rate / float(self.target_rate)

    def convert(self, pcm, final=False):
        incoming = np.frombuffer(pcm, dtype="<i2").astype(np.float32) if pcm else np.zeros(0, dtype=np.float32)
        if self.source_rate == self.target_rate:
            values = incoming
        else:
            if incoming.size:
                self._buffer = np.concatenate((self._buffer, incoming))
            limit = len(self._buffer) if final else max(0, len(self._buffer) - 1)
            if self._position >= limit or not len(self._buffer):
                values = np.zeros(0, dtype=np.float32)
            else:
                positions = np.arange(self._position, limit, self._step, dtype=np.float64)
                left = np.floor(positions).astype(np.int64)
                right = np.minimum(left + 1, len(self._buffer) - 1)
                fraction = positions - left
                values = self._buffer[left] * (1.0 - fraction) + self._buffer[right] * fraction
                next_position = float(positions[-1] + self._step)
                if final:
                    drop = min(int(next_position), len(self._buffer))
                else:
                    drop = min(int(next_position), max(0, len(self._buffer) - 1))
                self._buffer = self._buffer[drop:]
                self._position = next_position - drop
            if final:
                self._buffer = np.zeros(0, dtype=np.float32)
                self._position = 0.0

        if not len(values):
            return b""
        mono = np.clip(np.rint(values), -32768, 32767).astype("<i2")
        if self.target_channels == 1:
            return mono.tobytes()
        interleaved = np.repeat(mono[:, None], self.target_channels, axis=1)
        return np.ascontiguousarray(interleaved).tobytes()


class LiveAudioOutput(QObject):
    """Bounded push-mode QAudioSink with a small anti-underrun start buffer."""

    failed = Signal(str)
    drained = Signal()
    buffer_changed = Signal(float)

    START_BUFFER_SECONDS = 0.15
    SINK_BUFFER_SECONDS = 0.50
    MAX_PENDING_SECONDS = 30.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sink = None
        self._io = None
        self._device_key = None
        self._source_rate = None
        self._format = None
        self._converter = None
        self._pending = bytearray()
        self._started = False
        self._input_finished = False
        self._drained_emitted = False
        self._configured_at = None
        self._sink_started_at = None
        self._bytes_written = 0
        self._underruns = 0
        self._last_state = None
        self._last_stats = {}
        self._start_buffer_seconds = self.START_BUFFER_SECONDS
        self._sink_buffer_seconds = self.SINK_BUFFER_SECONDS
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def device_key(device):
        try:
            return bytes(device.id())
        except Exception:
            return str(device.description()).encode("utf-8", "replace")

    @staticmethod
    def _build_format(rate, channels, sample_format=QAudioFormat.SampleFormat.Int16):
        # Not "_format": that name is the instance attribute holding the negotiated
        # format, which shadowed this method and broke configure() on real devices.
        fmt = QAudioFormat()
        fmt.setSampleRate(int(rate))
        fmt.setChannelCount(int(channels))
        fmt.setSampleFormat(sample_format)
        return fmt

    @staticmethod
    def _bytes_per_second_for(fmt):
        return int(fmt.sampleRate()) * int(fmt.channelCount()) * max(1, int(fmt.bytesPerSample()))

    def _to_sink(self, pcm):
        """s16le from the converter -> the sink's sample format (float32 on most
        Windows shared-mode endpoints, which accept only their float mix format)."""
        if not pcm or self._format is None or self._format.sampleFormat() != QAudioFormat.SampleFormat.Float:
            return pcm
        return (np.frombuffer(pcm, dtype="<i2").astype("<f4") / np.float32(32768.0)).tobytes()

    def _choose_format(self, device, source_rate):
        preferred = device.preferredFormat()
        rates = []
        # Prefer one stable Windows-friendly sink rate so queued voices with
        # different model-native rates can share the same live output stream.
        for rate in (48000, preferred.sampleRate(), source_rate, 44100):
            rate = int(rate or 0)
            if rate > 0 and rate not in rates:
                rates.append(rate)
        channels = []
        for count in (1, preferred.channelCount(), 2):
            count = int(count or 0)
            if count in (1, 2) and count not in channels:
                channels.append(count)
        # Int16 first; then float32, which Qt's Windows backend often requires (it
        # accepts only the endpoint's shared-mode mix format, typically float stereo).
        for sample_format in (QAudioFormat.SampleFormat.Int16, QAudioFormat.SampleFormat.Float):
            for rate in rates:
                for count in channels:
                    fmt = self._build_format(rate, count, sample_format)
                    if device.isFormatSupported(fmt):
                        return fmt
        if (preferred.sampleFormat() in (QAudioFormat.SampleFormat.Int16, QAudioFormat.SampleFormat.Float)
                and preferred.channelCount() in (1, 2) and device.isFormatSupported(preferred)):
            return self._build_format(preferred.sampleRate(), preferred.channelCount(), preferred.sampleFormat())
        raise RuntimeError(
            f"{device.description()} does not advertise a compatible mono/stereo Int16 or float format."
        )

    @staticmethod
    def _same_format(a, b):
        return (
            a is not None and b is not None
            and a.sampleRate() == b.sampleRate()
            and a.channelCount() == b.channelCount()
            and a.sampleFormat() == b.sampleFormat()
        )

    def configure(
        self,
        device,
        source_rate,
        *,
        start_buffer_seconds=None,
        sink_buffer_seconds=None,
    ):
        key = self.device_key(device)
        source_rate = int(source_rate)
        self._start_buffer_seconds = max(
            0.0,
            float(
                self.START_BUFFER_SECONDS
                if start_buffer_seconds is None
                else start_buffer_seconds
            ),
        )
        self._sink_buffer_seconds = max(
            0.02,
            float(
                self.SINK_BUFFER_SECONDS
                if sink_buffer_seconds is None
                else sink_buffer_seconds
            ),
        )
        fmt = self._choose_format(device, source_rate)

        if self._sink is not None and key == self._device_key and self._same_format(fmt, self._format):
            if source_rate != self._source_rate:
                # Finish the old source-side resampler without resetting the sink;
                # this preserves audio already queued from the previous item.
                if self._converter is not None:
                    tail = self._converter.convert(b"", final=True)
                    if tail:
                        self._pending.extend(self._to_sink(tail))
                self._source_rate = source_rate
                self._converter = StreamingPcmConverter(
                    source_rate, fmt.sampleRate(), fmt.channelCount()
                )
            bytes_per_second = self._bytes_per_second_for(fmt)
            self._sink.setBufferSize(
                max(4096, int(bytes_per_second * self._sink_buffer_seconds))
            )
            self._input_finished = False
            self._drained_emitted = False
            self._last_stats = {}
            self._timer.start()
            return

        if self.is_playing():
            raise RuntimeError(
                "The next item needs a different output format while previous audio is still playing. "
                "Wait for the current audio to finish before switching this route."
            )

        self.stop()
        self._device_key = key
        self._source_rate = source_rate
        self._format = fmt
        self._converter = StreamingPcmConverter(
            source_rate, fmt.sampleRate(), fmt.channelCount()
        )
        self._sink = QAudioSink(device, fmt, self)
        self._sink.stateChanged.connect(self._on_state_changed)
        self._configured_at = time.monotonic()
        self._sink_started_at = None
        self._bytes_written = 0
        self._underruns = 0
        self._last_state = None
        self._last_stats = {}
        bytes_per_second = self._bytes_per_second_for(fmt)
        self._sink.setBufferSize(
            max(4096, int(bytes_per_second * self._sink_buffer_seconds))
        )
        self._pending.clear()
        self._started = False
        self._input_finished = False
        self._drained_emitted = False
        self._timer.start()

    def _bytes_per_second(self):
        if self._format is None:
            return 0
        return self._bytes_per_second_for(self._format)

    def _start_sink(self):
        if self._started or self._sink is None:
            return
        self._io = self._sink.start()
        if self._io is None:
            raise RuntimeError("Qt could not open the selected audio output device.")
        self._sink_started_at = time.monotonic()
        self._started = True

    def push(self, pcm):
        if self._sink is None or self._converter is None:
            raise RuntimeError("Live audio output is not configured.")
        converted = self._converter.convert(pcm)
        if converted:
            self._pending.extend(self._to_sink(converted))
        bps = self._bytes_per_second()
        if bps and len(self._pending) > int(bps * self.MAX_PENDING_SECONDS):
            raise RuntimeError("Live audio buffer exceeded 30 seconds; stopping instead of growing without bound.")
        if not self._started and (
            len(self._pending) >= int(bps * self._start_buffer_seconds)
        ):
            self._start_sink()
        self._flush()
        self.buffer_changed.emit(self.buffered_ms())

    def finish_input(self):
        if self._converter is not None:
            tail = self._converter.convert(b"", final=True)
            if tail:
                self._pending.extend(self._to_sink(tail))
        self._input_finished = True
        if self._pending and not self._started:
            self._start_sink()
        self._flush()

    def _flush(self):
        if not self._started or self._io is None or self._sink is None:
            return
        while self._pending:
            available = max(0, int(self._sink.bytesFree()))
            if available <= 0:
                break
            chunk = bytes(self._pending[:available])
            written = int(self._io.write(chunk))
            if written <= 0:
                break
            self._bytes_written += written
            del self._pending[:written]

    def _on_state_changed(self, state):
        if (
            self._last_state == QAudio.State.ActiveState
            and state == QAudio.State.IdleState
            and self._started
            and not self._input_finished
            and self._bytes_written > 0
        ):
            self._underruns += 1
        self._last_state = state

    def _tick(self):
        try:
            self._flush()
            self.buffer_changed.emit(self.buffered_ms())
            if (
                self._input_finished
                and not self._pending
                and self._sink is not None
                and self._started
                and self._sink.state() == QAudio.State.IdleState
                and not self._drained_emitted
            ):
                self._drained_emitted = True
                self._last_stats = self.stats()
                self.drained.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
            self.stop()

    def buffered_ms(self):
        bps = self._bytes_per_second()
        if not bps:
            return 0.0
        queued = len(self._pending)
        if self._sink is not None and self._started:
            size = max(0, int(self._sink.bufferSize()))
            free = max(0, int(self._sink.bytesFree()))
            queued += max(0, size - free)
        return 1000.0 * queued / bps

    def is_playing(self):
        if self._pending:
            return True
        if self._sink is None or not self._started:
            return False
        return self._sink.state() == QAudio.State.ActiveState or self.buffered_ms() > 1.0

    def stats(self):
        now = time.monotonic()
        target_rate = self._format.sampleRate() if self._format is not None else None
        channels = self._format.channelCount() if self._format is not None else None
        configured_to_start = None
        if self._configured_at is not None and self._sink_started_at is not None:
            configured_to_start = self._sink_started_at - self._configured_at
        return {
            "source_rate": self._source_rate,
            "target_rate": target_rate,
            "channels": channels,
            "sample_format": self._sample_format_label(),
            "buffered_ms": round(self.buffered_ms(), 2),
            "underruns": int(self._underruns),
            "bytes_written": int(self._bytes_written),
            "configured_to_start_seconds": (
                round(configured_to_start, 4) if configured_to_start is not None else None
            ),
            "active_seconds": (
                round(now - self._sink_started_at, 4)
                if self._sink_started_at is not None
                else None
            ),
            "started": bool(self._started),
            "input_finished": bool(self._input_finished),
            "start_buffer_seconds": round(self._start_buffer_seconds, 4),
            "sink_buffer_seconds": round(self._sink_buffer_seconds, 4),
        }

    def last_stats(self):
        return dict(self._last_stats or self.stats())

    def _sample_format_label(self):
        if self._format is None:
            return None
        return "f32" if self._format.sampleFormat() == QAudioFormat.SampleFormat.Float else "s16"

    def route_description(self):
        if self._format is None:
            return ""
        return (
            f"{self._format.sampleRate()} Hz · "
            f"{self._format.channelCount()} ch · {self._sample_format_label()}"
        )

    def stop(self):
        self._last_stats = self.stats()
        self._timer.stop()
        self._pending.clear()
        self._input_finished = False
        self._drained_emitted = False
        self._started = False
        self._io = None
        if self._sink is not None:
            try:
                self._sink.reset()
            except Exception:
                try:
                    self._sink.stop()
                except Exception:
                    pass
            self._sink.deleteLater()
        self._sink = None
        self._format = None
        self._converter = None
        self._device_key = None
        self._source_rate = None
        self.buffer_changed.emit(0.0)
