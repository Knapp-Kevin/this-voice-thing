"""Microphone source primitives for Live Voice.

This module contains no Qt device or routing code. It normalizes captured signed
16-bit PCM into mono AudioFrame objects and applies a small, deterministic DSP
chain suitable for low-latency microphone effects.
"""

from collections import deque
from dataclasses import asdict, dataclass
import math
import threading
import time

import numpy as np

from this_voice_thing.core.live_voice import AudioFrame


class PcmRingBuffer:
    """Thread-safe bounded PCM FIFO.

    Overflow drops the oldest complete chunks so latency stays bounded rather
    than growing forever. Dropped bytes are observable through stats().
    """

    def __init__(self, max_bytes):
        self.max_bytes = max(1, int(max_bytes))
        self._chunks = deque()
        self._bytes = 0
        self._dropped_bytes = 0
        self._closed = False
        self._condition = threading.Condition()

    def push(self, pcm):
        data = bytes(pcm or b"")
        if not data:
            return 0
        with self._condition:
            if self._closed:
                return 0
            if len(data) > self.max_bytes:
                self._dropped_bytes += len(data) - self.max_bytes
                data = data[-self.max_bytes:]
            while self._chunks and self._bytes + len(data) > self.max_bytes:
                removed = self._chunks.popleft()
                self._bytes -= len(removed)
                self._dropped_bytes += len(removed)
            self._chunks.append(data)
            self._bytes += len(data)
            self._condition.notify()
            return len(data)

    def pop(self, timeout=0.05):
        with self._condition:
            if not self._chunks and not self._closed:
                self._condition.wait(timeout=max(0.0, float(timeout)))
            if self._chunks:
                data = self._chunks.popleft()
                self._bytes -= len(data)
                return data
            return None

    def clear(self):
        with self._condition:
            self._chunks.clear()
            self._bytes = 0
            self._condition.notify_all()

    def close(self, discard=True):
        with self._condition:
            self._closed = True
            if discard:
                self._chunks.clear()
                self._bytes = 0
            self._condition.notify_all()

    @property
    def closed(self):
        with self._condition:
            return self._closed

    def stats(self):
        with self._condition:
            return {
                "pending_bytes": int(self._bytes),
                "dropped_bytes": int(self._dropped_bytes),
                "max_bytes": int(self.max_bytes),
                "closed": bool(self._closed),
            }


@dataclass(frozen=True)
class MicrophoneEffectsConfig:
    gain_db: float = 0.0
    tone: float = 0.0
    compressor_enabled: bool = False
    compressor_threshold_db: float = -18.0
    compressor_ratio: float = 3.0
    limiter_ceiling_db: float = 0.0

    def normalized(self):
        return MicrophoneEffectsConfig(
            gain_db=float(np.clip(self.gain_db, -24.0, 24.0)),
            tone=float(np.clip(self.tone, -1.0, 1.0)),
            compressor_enabled=bool(self.compressor_enabled),
            compressor_threshold_db=float(
                np.clip(self.compressor_threshold_db, -48.0, -1.0)
            ),
            compressor_ratio=float(np.clip(self.compressor_ratio, 1.0, 20.0)),
            limiter_ceiling_db=float(np.clip(self.limiter_ceiling_db, -12.0, 0.0)),
        )

    def as_dict(self):
        return asdict(self.normalized())

    def is_neutral(self):
        cfg = self.normalized()
        return (
            abs(cfg.gain_db) < 1e-9
            and abs(cfg.tone) < 1e-9
            and not cfg.compressor_enabled
            and cfg.limiter_ceiling_db >= -1e-9
        )


def downmix_s16le(pcm, channels):
    """Return mono signed-16 PCM from interleaved signed-16 PCM."""
    channels = int(channels)
    if channels <= 0:
        raise ValueError("Microphone channel count must be positive.")
    data = bytes(pcm or b"")
    if not data:
        return b""
    samples = np.frombuffer(data, dtype="<i2")
    usable = (len(samples) // channels) * channels
    if usable <= 0:
        return b""
    samples = samples[:usable]
    if channels == 1:
        return samples.astype("<i2", copy=False).tobytes()
    frames = samples.reshape(-1, channels).astype(np.int32)
    mono = np.rint(frames.mean(axis=1)).clip(-32768, 32767).astype("<i2")
    return mono.tobytes()


class MicrophoneEffectsProcessor:
    """Small stateful DSP chain designed for realtime microphone blocks."""

    def __init__(self, config=None):
        self.config = (config or MicrophoneEffectsConfig()).normalized()
        self._tone_state = 0.0

    def reset(self):
        self._tone_state = 0.0

    def _apply_tone(self, values, sample_rate):
        amount = self.config.tone
        if abs(amount) < 1e-9 or not len(values):
            return values

        rate = max(8000.0, float(sample_rate))
        cutoff = 2400.0
        alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / rate)
        low = np.empty_like(values)
        state = float(self._tone_state)
        for index, value in enumerate(values):
            state += alpha * (float(value) - state)
            low[index] = state
        self._tone_state = state

        strength = abs(amount)
        if amount < 0.0:
            # Warmer/darker: blend toward a low-passed signal.
            return values * (1.0 - strength) + low * strength

        # Brighter: gently reinforce the high-frequency residual.
        high = values - low
        return values + high * (0.65 * strength)

    def _apply_compressor(self, values):
        if not self.config.compressor_enabled or not len(values):
            return values
        magnitude = np.abs(values)
        safe = np.maximum(magnitude, 1e-7)
        level_db = 20.0 * np.log10(safe)
        threshold = self.config.compressor_threshold_db
        above = level_db > threshold
        if not np.any(above):
            return values
        compressed_db = threshold + (
            level_db[above] - threshold
        ) / self.config.compressor_ratio
        gain_db = compressed_db - level_db[above]
        gains = np.ones_like(values)
        gains[above] = np.power(10.0, gain_db / 20.0)
        return values * gains

    def process(self, pcm, sample_rate):
        data = bytes(pcm or b"")
        if not data:
            return b""
        if self.config.is_neutral():
            return data

        values = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        if abs(self.config.gain_db) >= 1e-9:
            values *= float(np.power(10.0, self.config.gain_db / 20.0))
        values = self._apply_tone(values, sample_rate)
        values = self._apply_compressor(values)

        ceiling = float(np.power(10.0, self.config.limiter_ceiling_db / 20.0))
        values = np.clip(values, -ceiling, ceiling)
        return np.rint(values * 32767.0).astype("<i2").tobytes()


class MicrophoneBlockProcessor:
    """Normalize one captured block and emit the shared Live Voice frame type."""

    def __init__(self, effects=None):
        self.effects = MicrophoneEffectsProcessor(effects)
        self.started_at = time.monotonic()
        self.blocks = 0
        self.input_bytes = 0
        self.output_bytes = 0
        self._remainder = b""
        self._remainder = b""

    @property
    def provenance(self):
        return (
            "microphone-passthrough"
            if self.effects.config.is_neutral()
            else "microphone-dsp"
        )

    def process(self, pcm, *, sample_rate, channels):
        raw = bytes(pcm or b"")
        self.input_bytes += len(raw)
        frame_bytes = max(2, int(channels) * 2)
        combined = self._remainder + raw
        usable = (len(combined) // frame_bytes) * frame_bytes
        self._remainder = combined[usable:]
        if usable <= 0:
            return None
        mono = downmix_s16le(combined[:usable], channels)
        processed = self.effects.process(mono, sample_rate)
        if not processed:
            return None
        self.blocks += 1
        self.output_bytes += len(processed)
        return AudioFrame(
            pcm=processed,
            sample_rate=int(sample_rate),
            channels=1,
            provenance=self.provenance,
        )

    def metrics(self):
        return {
            "mode": "microphone",
            "provenance": self.provenance,
            "blocks": int(self.blocks),
            "input_bytes": int(self.input_bytes),
            "output_bytes": int(self.output_bytes),
            "partial_frame_bytes": len(self._remainder),
            "active_seconds": round(time.monotonic() - self.started_at, 4),
            "effects": self.effects.config.as_dict(),
        }
