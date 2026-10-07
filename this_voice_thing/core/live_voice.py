"""Shared live-speech generation primitives.

Live Voice and the local HTTP API are separate consumers of the same model
capabilities. This module deliberately knows nothing about Qt audio devices or
HTTP. It turns one logical utterance into typed PCM frames and reports truthful
delivery metadata: native, segmented, or buffered.
"""

from dataclasses import dataclass
import time
import wave

import numpy as np
import soundfile as sf


@dataclass(frozen=True)
class AudioFrame:
    pcm: bytes
    sample_rate: int
    channels: int = 1
    sample_format: str = "s16le"
    end_of_segment: bool = False
    end_of_utterance: bool = False
    discontinuity: bool = False
    provenance: str = ""


class LiveSpeechSession:
    """One live utterance using a loaded/configured model.

    The caller owns model selection and voice configuration. A session snapshots
    the text and generation settings, chooses the best declared delivery mode,
    and yields mono signed-16 PCM frames.
    """

    def __init__(
        self,
        model,
        text,
        *,
        audio_prompt_path=None,
        language_id="en",
        paragraph_pause=0.35,
        pronunciations=None,
        generate_kwargs=None,
    ):
        self.model = model
        self.text = str(text or "").strip()
        self.audio_prompt_path = audio_prompt_path
        self.language_id = language_id or "en"
        self.paragraph_pause = float(paragraph_pause)
        self.pronunciations = pronunciations
        self.generate_kwargs = dict(generate_kwargs or {})
        self.sample_rate = int(getattr(model, "sr", 0) or 0)
        if self.sample_rate <= 0:
            raise ValueError("The loaded model does not expose a valid sample rate.")
        if not self.text:
            raise ValueError("Live speech text is empty.")

        native = getattr(model, "generate_streaming_pcm", None)
        segmented = getattr(model, "generate_segmented_pcm", None)
        if callable(native) and getattr(model, "native_streaming", False):
            self.delivery_mode = "native"
            self.provenance = "live-native-unwatermarked"
        elif callable(segmented) and getattr(model, "segmented_streaming", False):
            self.delivery_mode = "segmented"
            self.provenance = "live-segmented-engine-watermark"
        else:
            self.delivery_mode = "buffered"
            self.provenance = "buffered-engine-default"

        self._started_at = None
        self._first_audio_at = None
        self._ended_at = None
        self._audio_bytes = 0
        self._frames = 0
        self.cancelled = False

    def _spoken_text(self):
        text = self.text
        if self.pronunciations is not None:
            text, _count = self.pronunciations.apply_section(text)
        return text

    @staticmethod
    def _tensor_to_pcm(value):
        if hasattr(value, "detach"):
            value = value.squeeze(0).detach().cpu().numpy()
        wav = np.asarray(value, dtype=np.float32).reshape(-1)
        if not len(wav):
            return b""
        return (
            np.clip(wav, -1.0, 1.0) * 32767.0
        ).astype("<i2", copy=False).tobytes()

    def _source(self, spoken_text):
        if self.delivery_mode == "native":
            return self.model.generate_streaming_pcm(
                spoken_text,
                audio_prompt_path=self.audio_prompt_path,
            )
        if self.delivery_mode == "segmented":
            return self.model.generate_segmented_pcm(
                spoken_text,
                language_id=self.language_id,
                paragraph_pause=self.paragraph_pause,
            )

        kwargs = dict(self.generate_kwargs)
        kwargs.setdefault("audio_prompt_path", self.audio_prompt_path)
        kwargs.setdefault("language_id", self.language_id)
        try:
            result = self.model.generate(spoken_text, **kwargs)
        except TypeError:
            # Older/simple backends intentionally accept fewer knobs.
            result = self.model.generate(spoken_text)
        pcm = self._tensor_to_pcm(result)
        return iter((pcm,)) if pcm else iter(())

    def frames(self, cancelled=lambda: False):
        """Yield AudioFrame objects until complete or cancellation is requested."""
        self._started_at = time.monotonic()
        spoken_text = self._spoken_text()
        source = self._source(spoken_text)
        try:
            for pcm in source:
                if cancelled():
                    self.cancelled = True
                    break
                if not pcm:
                    continue
                if self._first_audio_at is None:
                    self._first_audio_at = time.monotonic()
                self._frames += 1
                self._audio_bytes += len(pcm)
                yield AudioFrame(
                    pcm=bytes(pcm),
                    sample_rate=self.sample_rate,
                    provenance=self.provenance,
                )
        finally:
            close = getattr(source, "close", None)
            if close is not None:
                close()
            self._ended_at = time.monotonic()

    def metrics(self):
        started = self._started_at
        ended = self._ended_at or time.monotonic()
        wall = (ended - started) if started is not None else 0.0
        audio_seconds = self._audio_bytes / float(self.sample_rate * 2)
        return {
            "mode": self.delivery_mode,
            "sample_rate": self.sample_rate,
            "frames": self._frames,
            "audio_seconds": round(audio_seconds, 4),
            "generation_seconds": round(wall, 4),
            "ttfa_seconds": (
                round(self._first_audio_at - started, 4)
                if started is not None and self._first_audio_at is not None
                else None
            ),
            "rtf": round(wall / audio_seconds, 4) if audio_seconds else None,
            "cancelled": bool(self.cancelled),
            "provenance": self.provenance,
        }


class CachedSpeechSession:
    """Read a cached mono s16 WAV through the same frame interface as live TTS."""

    def __init__(self, path, label="Cached soundboard", provenance="soundboard-cache-unknown"):
        self.path = path
        self.label = label
        with wave.open(path, "rb") as handle:
            if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
                raise ValueError("Cached soundboard audio must be mono 16-bit PCM WAV.")
            self.sample_rate = int(handle.getframerate())
        self.delivery_mode = "cached"
        self.provenance = str(provenance or "soundboard-cache-unknown")
        self._started_at = None
        self._first_audio_at = None
        self._ended_at = None
        self._audio_bytes = 0
        self._frames = 0
        self.cancelled = False

    def frames(self, cancelled=lambda: False):
        self._started_at = time.monotonic()
        try:
            with wave.open(self.path, "rb") as handle:
                while True:
                    if cancelled():
                        self.cancelled = True
                        break
                    pcm = handle.readframes(4096)
                    if not pcm:
                        break
                    if self._first_audio_at is None:
                        self._first_audio_at = time.monotonic()
                    self._frames += 1
                    self._audio_bytes += len(pcm)
                    yield AudioFrame(
                        pcm=pcm,
                        sample_rate=self.sample_rate,
                        provenance=self.provenance,
                    )
        finally:
            self._ended_at = time.monotonic()

    def metrics(self):
        started = self._started_at
        ended = self._ended_at or time.monotonic()
        wall = (ended - started) if started is not None else 0.0
        audio_seconds = self._audio_bytes / float(self.sample_rate * 2)
        return {
            "mode": self.delivery_mode,
            "sample_rate": self.sample_rate,
            "frames": self._frames,
            "audio_seconds": round(audio_seconds, 4),
            "generation_seconds": round(wall, 4),
            "ttfa_seconds": (
                round(self._first_audio_at - started, 4)
                if started is not None and self._first_audio_at is not None
                else None
            ),
            "rtf": round(wall / audio_seconds, 4) if audio_seconds else None,
            "cancelled": bool(self.cancelled),
            "provenance": self.provenance,
        }


class AudioFileSpeechSession:
    """Stream a local audio file through the same mono s16 PCM interface as Live Voice."""

    def __init__(self, path, label="Soundboard audio"):
        self.path = path
        self.label = label
        info = sf.info(path)
        self.sample_rate = int(info.samplerate or 0)
        if self.sample_rate <= 0:
            raise ValueError("Audio file does not expose a valid sample rate.")
        if int(info.channels or 0) <= 0:
            raise ValueError("Audio file does not expose a valid channel count.")
        self.delivery_mode = "audio"
        self.provenance = "soundboard-audio-file"
        self._started_at = None
        self._first_audio_at = None
        self._ended_at = None
        self._audio_bytes = 0
        self._frames = 0
        self.cancelled = False

    @staticmethod
    def _float_to_pcm(values):
        mono = np.asarray(values, dtype=np.float32).reshape(-1)
        if not len(mono):
            return b""
        return (
            np.clip(mono, -1.0, 1.0) * 32767.0
        ).astype("<i2", copy=False).tobytes()

    def frames(self, cancelled=lambda: False):
        self._started_at = time.monotonic()
        try:
            with sf.SoundFile(self.path, "r") as handle:
                while True:
                    if cancelled():
                        self.cancelled = True
                        break
                    values = handle.read(frames=4096, dtype="float32", always_2d=True)
                    if not len(values):
                        break
                    mono = values.mean(axis=1)
                    pcm = self._float_to_pcm(mono)
                    if not pcm:
                        continue
                    if self._first_audio_at is None:
                        self._first_audio_at = time.monotonic()
                    self._frames += 1
                    self._audio_bytes += len(pcm)
                    yield AudioFrame(
                        pcm=pcm,
                        sample_rate=self.sample_rate,
                        provenance=self.provenance,
                    )
        finally:
            self._ended_at = time.monotonic()

    def metrics(self):
        started = self._started_at
        ended = self._ended_at or time.monotonic()
        wall = (ended - started) if started is not None else 0.0
        audio_seconds = self._audio_bytes / float(self.sample_rate * 2)
        return {
            "mode": self.delivery_mode,
            "sample_rate": self.sample_rate,
            "frames": self._frames,
            "audio_seconds": round(audio_seconds, 4),
            "generation_seconds": round(wall, 4),
            "ttfa_seconds": (
                round(self._first_audio_at - started, 4)
                if started is not None and self._first_audio_at is not None
                else None
            ),
            "rtf": round(wall / audio_seconds, 4) if audio_seconds else None,
            "cancelled": bool(self.cancelled),
            "provenance": self.provenance,
        }
