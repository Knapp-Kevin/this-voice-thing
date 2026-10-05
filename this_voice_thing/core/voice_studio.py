"""Voice Studio: make, refine and freeze voices with whichever model is loaded.

Every finished voice is frozen the same way: a clip plus the transcript of what it
says. Any cloning model can speak a frozen voice, and a design model clones from it
(as its locked anchor), so the voice sounds the same in every render and batch.

The functions here drive the app's model objects directly and leave them as they
found them (style, transcript, anchors), so the Generate page isn't disturbed.
"""

import os
import random
from dataclasses import dataclass

import numpy as np

# A designed voice is frozen from this read: about 10 s of varied, phonetically rich
# speech, so the clip makes a good reference for every cloning model afterwards.
DESIGN_PASSAGE = ("Would you hand me the yellow measuring cup before the soup boils over? "
                  "The quick thinker judged each chance, then sighed with real pleasure.")
TEST_LINE = "The tide waits for no one, so let's get this story started."
MAX_CANDIDATES = 4
MODES = ("clone", "design", "remix")


@dataclass
class Take:
    path: str
    text: str                # what the take says (its transcript)
    label: str
    origin: str              # "clone" | "design" | "remix" | "refined"
    seconds: float = 0.0


def to_numpy(wav):
    data = wav.squeeze(0).detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
    return np.asarray(data, dtype=np.float32).reshape(-1)


def save_take(directory, stem, wav, sr):
    import soundfile
    os.makedirs(directory, exist_ok=True)
    index = 1
    while os.path.exists(os.path.join(directory, f"{stem}_{index}.wav")):
        index += 1
    path = os.path.join(directory, f"{stem}_{index}.wav")
    soundfile.write(path, np.clip(wav, -1.0, 1.0), sr, subtype="PCM_16")
    return path


def is_design_model(model):
    return getattr(model, "mode", "") == "voice_design"


def is_worker_model(model):
    """The engines that run in their own environment (they carry style and transcript fields)."""
    return hasattr(model, "instruct") and hasattr(model, "ref_text")


def supports_style(model):
    """Cloning that can be steered with words: VoxCPM's styled cloning."""
    return getattr(model, "backend", "") == "voxcpm" and getattr(model, "mode", "") == "base"


class _KeepState:
    """Restores the model fields the Studio changes, whatever happens."""

    FIELDS = ("instruct", "ref_text", "locked_anchor", "_anchor")

    def __init__(self, model):
        self.model = model
        self.saved = {name: getattr(model, name) for name in self.FIELDS if hasattr(model, name)}

    def __enter__(self):
        return self.model

    def __exit__(self, *_exc):
        for name, value in self.saved.items():
            setattr(self.model, name, value)


def _seed(seed):
    import torch
    seed = random.randint(1, 2**31 - 1) if seed is None else int(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    return seed


def design(model, description, text=DESIGN_PASSAGE, language_id=None, seed=None):
    """A brand-new voice from a description (a design model invents one per call).
    Returns (wav, sample rate, seed)."""
    if not is_design_model(model):
        raise ValueError("Load a voice design model to design voices.")
    if not description.strip():
        raise ValueError("Describe the voice first.")
    with _KeepState(model):
        model.locked_anchor = None
        model.instruct = description.strip()
        model.begin_run()
        seed = _seed(seed)
        wav = model.generate(text, language_id=language_id)
    return to_numpy(wav), int(model.sr), seed


def speak(model, text, clip, transcript="", style="", language_id=None, seed=None, delivery=None):
    """Say `text` in the voice of `clip`, with any loaded model that can clone.
    Design models clone from the clip as their locked anchor; VoxCPM's cloning can
    also take a `style` ("older, slower, warmer"). `delivery` (Chatterbox) holds
    exaggeration, cfg_weight and temperature. Returns (wav, sample rate, seed)."""
    if not clip or not os.path.exists(clip):
        raise ValueError("There's no clip to speak with.")
    delivery = dict(delivery or {})
    with _KeepState(model):
        seed = _seed(seed)
        if is_design_model(model):
            model.locked_anchor = (clip, transcript)
            model.begin_run()
            wav = model.generate(text, language_id=language_id)
        elif is_worker_model(model):
            if getattr(model, "mode", "") != "base":
                raise ValueError(f"{getattr(model, 'repo_id', 'This model')} doesn't clone voices.")
            model.instruct = style.strip() if supports_style(model) else ""
            model.ref_text = transcript
            if hasattr(model, "begin_run"):
                model.begin_run()
            wav = model.generate(text, audio_prompt_path=clip, language_id=language_id, **delivery)
        else:  # Chatterbox, in this process
            try:
                wav = model.generate(text, audio_prompt_path=clip, language_id=language_id, **delivery)
            except TypeError:  # the original English model takes no language
                wav = model.generate(text, audio_prompt_path=clip, **delivery)
    return to_numpy(wav), int(model.sr), seed


def process_clip(wav, sr, start=0.0, end=None, trim=False, level=False, speed=1.0, semitones=0.0, log=print):
    """Clean up and adjust a clip: keep start..end seconds, trim silent edges, even out
    the volume, then change speed and pitch (formant-preserving when FFmpeg has Rubber
    Band). The result is what gets frozen, so later renders start from it."""
    from this_voice_thing.core import audio_effects
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    first = max(0, int(round(start * sr)))
    last = len(wav) if end is None or end <= 0 else min(len(wav), int(round(end * sr)))
    if last - first < int(0.5 * sr):
        raise ValueError("Keep at least half a second of the clip.")
    wav = wav[first:last]
    if trim:
        wav = audio_effects.trim_silence(wav, sr)
    if abs(speed - 1.0) > 1e-3 or abs(semitones) > 1e-3:
        wav = audio_effects.stretch_and_shift(wav, sr, speed, semitones, log)
    if level:
        wav = audio_effects.even_volume(wav)
    return np.clip(wav, -1.0, 1.0).astype(np.float32)


def load_audio(path):
    """Mono float32 and its sample rate."""
    import soundfile
    wav, sr = soundfile.read(path, dtype="float32", always_2d=True)
    return wav.mean(axis=1), int(sr)
