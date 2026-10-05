"""Main-app side of the Qwen3-TTS engine.

Qwen3-TTS needs transformers 4.57.x while Chatterbox pins 5.2.0, so Qwen runs
in its own environment (engines/qwen/.venv) as a worker process. QwenModel
exposes the same interface as a loaded Chatterbox model (sr, device,
generate(...)), so sectioning, previews, progress and finishing work unchanged.
"""

import os
import tempfile

import numpy as np
import soundfile as sf
import torch

from this_voice_thing.engines import worker as engine_worker

ENGINE_DIR = engine_worker.engine_dir("qwen")
PYTHON = engine_worker.venv_python("qwen")
WORKER = os.path.join(ENGINE_DIR, "qwen_worker.py")
# Measured on an RTX 5070 Ti (1.7B, bf16): 12 sections took 181 s one at a time and 18 s as
# one batch, with peak VRAM rising only ~0.2 GB per extra section. The worker halves a batch
# and retries if the GPU runs out of memory.
BATCH_SIZE = 16
# Qwen reads long text well, so sections can be whole paragraphs: fewer seams, and
# intonation flows through each paragraph (Chatterbox stays at 280). Beyond ~600
# characters, delivery length starts to vary noticeably between runs.
MAX_SECTION_CHARS = 600
# Cloned voices (clones, and designed voices cloned from their saved clip) drift away
# from the reference over a long section and snap back at the next one, which is
# audible as a "reset". Measured on a designed voice: 300-character sections removed
# the drift and gave the smoothest seams (2026-10-05). Batching keeps them fast.
CLONE_SECTION_CHARS = 300
# A steadier default for cloned voices: Variation 0.5 matched the reference better
# than 0.8 (the slider's default for every engine).
CLONE_VARIATION = 0.5
# GPU memory grows with sections x section length (~1.2 GB per 1,000 characters on
# the 1.7B model), so batches are also capped by characters: about 8 paragraph-length
# sections or 16 short ones, peaking around 10 GB.
BATCH_CHAR_BUDGET = 5000

VARIANTS = {
    "custom_voice": "Preset voices with style instructions",
    "voice_design": "Design a new voice from a description",
    "base": "Clone a voice from a reference clip",
}
# App language codes -> Qwen language names (Qwen3-TTS covers 10 languages).
LANGUAGE_NAMES = {
    "en": "english", "zh": "chinese", "ja": "japanese", "ko": "korean", "de": "german",
    "fr": "french", "ru": "russian", "pt": "portuguese", "es": "spanish", "it": "italian",
}
LANGUAGE_LABELS = {code: name.capitalize() for code, name in LANGUAGE_NAMES.items()}


def is_installed():
    return os.path.exists(PYTHON) and os.path.exists(WORKER)


def install(log=print):
    """Create engines/qwen/.venv with CUDA PyTorch and qwen-tts using uv."""
    engine_worker.install_env("qwen", [["qwen-tts"]], log)


def QwenWorker(log=print):
    if not is_installed():
        raise RuntimeError("The Qwen engine is not installed.")
    return engine_worker.WorkerProcess(
        PYTHON, WORKER, "qwen", log,
        skip=lambda line: "SoX could not be found" in line or line.startswith(("*", " - - -")))


class QwenModel:
    """Chatterbox-compatible facade over a Qwen worker."""

    backend = "qwen3"

    def __init__(self, repo_id, worker, mode, speakers, languages):
        self.repo_id = repo_id
        self.worker = worker
        self.mode = mode
        self.speakers = speakers
        self.languages = languages
        self.sr = 24000
        self.device = worker.device
        self.supported_languages = {code: LANGUAGE_LABELS[code] for code, name in LANGUAGE_NAMES.items()
                                    if not languages or name in languages}
        # Sections generated per worker call. Decoding one sequence leaves the GPU mostly
        # idle (per-step overhead dominates), so batches are several times faster.
        self.batch_size = BATCH_SIZE if self.device == "cuda" else 1
        self.max_section_chars = CLONE_SECTION_CHARS if mode in ("base", "voice_design") else MAX_SECTION_CHARS
        self.batch_char_budget = BATCH_CHAR_BUDGET
        # Set by the UI before each generation.
        self.speaker = speakers[0] if speakers else None
        self.instruct = ""
        self.ref_text = ""
        self.watermark = True
        self._watermark = engine_worker.PerthWatermark()
        self._temp_dir = tempfile.mkdtemp(prefix="qwen_tts_")
        self._count = 0
        # Voice design invents a new voice on every call (and for every item in a batch),
        # so a run designs the first section only and clones the rest from it with the
        # matching Base model: Qwen's "design, then clone" workflow.
        self.clone_repo = repo_id.replace("VoiceDesign", "Base") if "VoiceDesign" in repo_id else None
        self.locked_anchor = None  # (clip, transcript) of a kept voice: every section clones it
        self._anchor = None

    def begin_run(self):
        self._anchor = self.locked_anchor

    def to(self, _device):
        return self

    def generate(self, text, **kwargs):
        return self.generate_batch([text], **kwargs)[0]

    def generate_batch(self, texts, audio_prompt_path=None, exaggeration=0.5, temperature=0.8,
                       cfg_weight=0.5, language_id=None, repetition_penalty=1.2, min_p=0.05,
                       top_p=1.0):
        """Generate several sections in one worker call; returns one (1, n) tensor each."""
        sampling = dict(language=LANGUAGE_NAMES.get(language_id or "", None),
                        seed=int(torch.initial_seed() % 2**31), temperature=float(temperature),
                        top_p=float(top_p), repetition_penalty=float(repetition_penalty))
        texts = list(texts)
        if self.mode == "voice_design" and self.clone_repo:
            paths = []
            if self._anchor is None:
                paths += self._request(texts[:1], "voice_design", sampling, instruct=self.instruct.strip())
                self._anchor = (paths[0], texts[0])
                texts = texts[1:]
            if texts:
                paths += self._request(texts, "base", sampling, clone_model_id=self.clone_repo,
                                       ref_audio=self._anchor[0], ref_text=self._anchor[1])
        else:
            paths = self._request(texts, self.mode, sampling, speaker=self.speaker,
                                  instruct=self.instruct.strip(), ref_audio=audio_prompt_path,
                                  ref_text=self.ref_text.strip() or None)
        results = []
        for path in paths:
            wav, sr = sf.read(path, dtype="float32")
            self.sr = sr
            if self.watermark:
                wav = self._watermark.apply(wav, sr, "Qwen")
            results.append(torch.from_numpy(np.ascontiguousarray(wav)).unsqueeze(0))
        return results

    def _request(self, texts, mode, sampling, **voice):
        out_paths = []
        for _text in texts:
            self._count += 1  # unique names: a designed voice's first section is kept as its anchor
            out_paths.append(os.path.join(self._temp_dir, f"section_{self._count}.wav"))
        reply = self.worker.request(cmd="generate", mode=mode, texts=texts, out_paths=out_paths,
                                    **sampling, **voice)
        return reply["paths"]

    def close(self):
        self.worker.close()


def load_qwen_model(repo_id, log=print):
    worker = QwenWorker(log=log)
    try:
        info = worker.request(cmd="load", model_id=repo_id)
    except Exception:
        worker.close()
        raise
    return QwenModel(repo_id, worker, info.get("mode"), info.get("speakers") or [],
                     info.get("languages") or [])
