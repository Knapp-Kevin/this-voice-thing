"""Main-app side of the Kokoro engine (hexgrad/Kokoro-82M, Apache-2.0).

Kokoro is a small (82M-parameter) model with built-in voices. Its text front
end (misaki, spaCy, espeak-ng) pulls in packages that don't belong in the
Chatterbox environment, so it runs in engines/kokoro/.venv as a worker.
KokoroModel exposes the same interface as a loaded Chatterbox model.
"""

import os
import tempfile

import numpy as np
import soundfile as sf
import torch

from this_voice_thing.core import audio_effects, documents
from this_voice_thing.engines import worker as engine_worker

NAME = "kokoro"
PYTHON = engine_worker.venv_python(NAME)
WORKER = os.path.join(engine_worker.engine_dir(NAME), "kokoro_worker.py")
SPACY_MODEL = ("en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/"
               "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl")
# Without the transformers floor the resolver backtracks to a 2021 release that needs Rust.
PACKAGES = [["kokoro==0.9.4", "misaki[en,zh]==0.9.4", "transformers>=4.45,<5", "soundfile"],
            [SPACY_MODEL]]
# Kokoro splits long text itself (510 phonemes per chunk), so a section can be a
# short paragraph; prosody is per sentence either way.
MAX_SECTION_CHARS = 400
LIVE_SECTION_CHARS = 180
SAMPLE_RATE = 24000

# Voice-name prefix -> (app language code, display name). The second letter is f/m.
VOICE_LANGUAGES = {
    "a": ("en", "US English"), "b": ("en", "UK English"), "e": ("es", "Spanish"),
    "f": ("fr", "French"), "h": ("hi", "Hindi"), "i": ("it", "Italian"),
    "p": ("pt", "Portuguese (Brazil)"), "z": ("zh", "Mandarin Chinese"),
}
LANGUAGE_LABELS = {"en": "English", "es": "Spanish", "fr": "French", "hi": "Hindi",
                   "it": "Italian", "pt": "Portuguese", "zh": "Chinese"}


def is_installed():
    return os.path.exists(PYTHON) and os.path.exists(WORKER)


def install(log=print):
    engine_worker.install_env(NAME, PACKAGES, log)


def voice_language(voice):
    return VOICE_LANGUAGES.get(voice[:1], (None, "Other"))[0]


def voice_label(voice):
    """'af_heart' -> 'Heart (US English, female)'."""
    prefix, _sep, name = voice.partition("_")
    language = VOICE_LANGUAGES.get(prefix[:1], (None, "other"))[1]
    gender = {"f": "female", "m": "male"}.get(prefix[1:2], "")
    details = ", ".join(part for part in (language, gender) if part)
    return f"{name.replace('_', ' ').title()} ({details})"


class KokoroModel:
    """Chatterbox-compatible facade over a Kokoro worker."""

    backend = NAME
    mode = "preset"

    def __init__(self, repo_id, worker, voices):
        self.repo_id = repo_id
        self.worker = worker
        self.speakers = [voice for voice in voices if voice_language(voice)]
        self.sr = SAMPLE_RATE
        self.device = worker.device
        self.segmented_streaming = True
        self.supported_languages = {code: LANGUAGE_LABELS[code] for code in LANGUAGE_LABELS
                                    if any(voice_language(v) == code for v in self.speakers)}
        self.batch_size = 1
        self.max_section_chars = MAX_SECTION_CHARS
        # Set by the UI before each generation.
        self.speaker = "af_heart" if "af_heart" in self.speakers else (self.speakers or [None])[0]
        self.instruct = ""
        self.ref_text = ""
        self.watermark = True
        self._watermark = engine_worker.PerthWatermark()
        self._temp_dir = tempfile.mkdtemp(prefix="kokoro_tts_")

    @staticmethod
    def speaker_label(voice):
        return voice_label(voice)

    def voices_for(self, language_id):
        return [voice for voice in self.speakers if voice_language(voice) == language_id]

    def to(self, _device):
        return self

    def generate(self, text, **kwargs):
        return self.generate_batch([text], **kwargs)[0]

    def generate_batch(self, texts, language_id=None, **_ignored):
        voice = self.speaker
        if language_id and voice_language(voice) != language_id:
            voice = (self.voices_for(language_id) or [voice])[0]
        out_paths = [os.path.join(self._temp_dir, f"section_{index}.wav") for index in range(len(texts))]
        reply = self.worker.request(cmd="generate", texts=list(texts), voice=voice, out_paths=out_paths)
        results = []
        for path in reply["paths"]:
            wav, sr = sf.read(path, dtype="float32")
            self.sr = sr
            if self.watermark:
                wav = self._watermark.apply(wav, sr, "Kokoro")
            results.append(torch.from_numpy(np.ascontiguousarray(wav)).unsqueeze(0))
        return results

    def generate_segmented_pcm(self, text, language_id=None, paragraph_pause=0.35):
        """Yield completed short Kokoro sections as mono s16le PCM.

        Kokoro does not expose acoustic-token streaming here, so this live mode
        generates one short speech section at a time. Each section still uses
        the selected Kokoro voice and the normal per-section AI watermark.
        Existing seam-gap rules are appended between sections so live playback
        keeps the same clause/sentence/paragraph rhythm as completed renders.
        """
        sections = documents.plan_sections(text, LIVE_SECTION_CHARS)
        for index, section in enumerate(sections):
            tensor = self.generate(section.text, language_id=language_id)
            wav = tensor.squeeze(0).detach().cpu().numpy() if hasattr(tensor, "detach") else np.asarray(tensor)
            wav = np.asarray(wav, dtype=np.float32).reshape(-1)
            wav = audio_effects.trim_silence(wav, self.sr)
            if index < len(sections) - 1:
                gap = audio_effects.seam_gap(section.boundary, paragraph_pause)
                if gap > 0:
                    wav = np.concatenate([wav, np.zeros(int(round(gap * self.sr)), dtype=np.float32)])
            pcm = (np.clip(wav, -1.0, 1.0) * 32767.0).astype("<i2", copy=False).tobytes()
            if pcm:
                yield pcm

    def close(self):
        self.worker.close()


def load_kokoro_model(repo_id, log=print):
    if not is_installed():
        raise RuntimeError("The Kokoro engine isn't installed. Click its tile on the Model page to install it.")
    worker = engine_worker.WorkerProcess(PYTHON, WORKER, "kokoro", log,
                                         skip=lambda line: "words count mismatch" in line
                                         or "warn" in line.lower())
    try:
        info = worker.request(cmd="load", model_id=repo_id)
    except Exception:
        worker.close()
        raise
    model = KokoroModel(repo_id, worker, info.get("voices") or [])
    if not model.speakers:
        worker.close()
        raise RuntimeError(f"{repo_id} has no voices in a language Kokoro supports here "
                           f"({', '.join(LANGUAGE_LABELS.values())}).")
    return model
