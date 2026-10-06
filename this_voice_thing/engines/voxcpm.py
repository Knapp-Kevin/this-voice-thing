"""Main-app side of the VoxCPM engine (openbmb/VoxCPM2, Apache-2.0).

VoxCPM2 is one 2B-parameter model that both clones a voice (optionally steered
by a style, and closer with a transcript of the clip) and designs a voice from
a description. It pulls in a large dependency set (FunASR, ModelScope, Gradio),
so it runs in engines/voxcpm/.venv as a worker. VoxCPMModel exposes the same
interface as a loaded Chatterbox model.

One loaded model serves both modes, so the "voice cloning" and "voice design"
entries switch instantly once either is loaded.
"""

import os
import tempfile

import numpy as np
import soundfile as sf
import torch

from this_voice_thing.engines import worker as engine_worker

NAME = "voxcpm"
PYTHON = engine_worker.venv_python(NAME)
WORKER = os.path.join(engine_worker.engine_dir(NAME), "voxcpm_worker.py")
# Pinning torch keeps the CUDA build installed first; plain PyPI would swap in a CPU build.
PACKAGES = [["voxcpm==2.0.3", "torch==2.8.0", "torchaudio==2.8.0"]]
# The model reads long text, but very long inputs can turn unstable; a short
# paragraph per section keeps delivery steady.
MAX_SECTION_CHARS = 400
MODES = {"clone": "Voice cloning", "design": "Voice design"}

# VoxCPM2 detects the language from the text; this list only drives the Language box.
LANGUAGE_LABELS = {
    "ar": "Arabic", "my": "Burmese", "zh": "Chinese", "da": "Danish", "nl": "Dutch",
    "en": "English", "fi": "Finnish", "fr": "French", "de": "German", "el": "Greek",
    "he": "Hebrew", "hi": "Hindi", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "km": "Khmer", "ko": "Korean", "lo": "Lao", "ms": "Malay", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "es": "Spanish", "sw": "Swahili",
    "sv": "Swedish", "tl": "Tagalog", "th": "Thai", "tr": "Turkish", "vi": "Vietnamese",
}


def is_installed():
    return os.path.exists(PYTHON) and os.path.exists(WORKER)


def install(log=print):
    engine_worker.install_env(NAME, PACKAGES, log)


class VoxCPMModel:
    """Chatterbox-compatible facade over a VoxCPM worker."""

    backend = NAME

    def __init__(self, repo_id, worker, sample_rate, mode, v2=True):
        self.repo_id = repo_id
        self.worker = worker
        self.sr = sample_rate
        self.device = worker.device
        self.native_streaming = bool(v2)
        self.last_stream_metrics = {}
        self.supported_languages = dict(LANGUAGE_LABELS)
        self.speakers = []
        self.speaker = None
        self.batch_size = 1
        self.max_section_chars = MAX_SECTION_CHARS
        self.set_mode(mode)
        # Set by the UI before each generation.
        self.instruct = ""   # style (cloning) or voice description (design)
        self.ref_text = ""   # transcript of the reference clip, for closer cloning
        self.watermark = True
        self.cfg_value = 2.0
        self.timesteps = 10
        self._watermark = engine_worker.PerthWatermark()
        self._temp_dir = tempfile.mkdtemp(prefix="voxcpm_tts_")
        self._anchor = None
        self.locked_anchor = None  # (clip, transcript) of a kept designed voice

    def set_mode(self, mode):
        # "base" and "voice_design" match the Qwen modes the UI already knows.
        self.voxcpm_mode = mode if mode in MODES else "clone"
        self.mode = "base" if self.voxcpm_mode == "clone" else "voice_design"

    def begin_run(self):
        """Called before each preview or render. A designed voice is invented afresh by
        every call, so the first section of a run becomes the reference for the rest,
        keeping one voice for the whole document. A kept voice is used from the start."""
        self._anchor = self.locked_anchor

    def to(self, _device):
        return self

    def generate(self, text, **kwargs):
        return self.generate_batch([text], **kwargs)[0]

    def _generation_request(self, text, audio_prompt_path=None, cmd="generate", out_path=None):
        request = dict(cmd=cmd, text=text, style="",
                       seed=int(torch.initial_seed() % 2**31),
                       cfg_value=self.cfg_value, timesteps=self.timesteps)
        if out_path is not None:
            request["out_path"] = out_path
        if self.voxcpm_mode == "design":
            if self._anchor is None:
                request["style"] = self.instruct.strip()
            else:
                request.update(prompt_wav=self._anchor[0], prompt_text=self._anchor[1],
                               reference_wav=self._anchor[0])
        else:
            request.update(reference_wav=audio_prompt_path, style=self.instruct.strip())
            if self.ref_text.strip() and audio_prompt_path:
                request.update(prompt_wav=audio_prompt_path, prompt_text=self.ref_text.strip())
        return request

    def generate_batch(self, texts, audio_prompt_path=None, **_ignored):
        results = []
        for text in texts:
            path = os.path.join(self._temp_dir, f"section_{len(os.listdir(self._temp_dir))}.wav")
            request = self._generation_request(text, audio_prompt_path, out_path=path)
            reply = self.worker.request(**request)
            wav, sr = sf.read(reply["path"], dtype="float32")
            self.sr = sr
            if self.voxcpm_mode == "design" and self._anchor is None:
                self._anchor = (reply["path"], text)
            if self.watermark:
                wav = self._watermark.apply(wav, sr, "VoxCPM")
            results.append(torch.from_numpy(np.ascontiguousarray(wav)).unsqueeze(0))
        return results

    def generate_streaming_pcm(self, text, audio_prompt_path=None):
        """Yield model-native mono s16le PCM chunks from VoxCPM2.

        This is intentionally a raw/live path: whole-waveform finishing and the
        Perth watermark are not applied. The HTTP API advertises that distinction
        explicitly rather than silently pretending streamed audio is identical to
        a completed render.
        """
        if not self.native_streaming:
            raise RuntimeError("Native PCM streaming is available only for VoxCPM2.")
        request = self._generation_request(text, audio_prompt_path, cmd="generate_stream")
        self.last_stream_metrics = {}
        events = self.worker.request_stream(**request)
        try:
            for event in events:
                kind = event.get("event")
                if kind == "audio":
                    yield base64.b64decode(event["data"])
                elif kind == "done":
                    self.last_stream_metrics = dict(event)
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                close()

    def close(self):
        self.worker.close()


def load_voxcpm_model(repo_id, mode="clone", log=print):
    if not is_installed():
        raise RuntimeError("The VoxCPM engine isn't installed. Click its tile on the Model page to install it.")
    worker = engine_worker.WorkerProcess(
        PYTHON, WORKER, "voxcpm", log,
        skip=lambda line: "warn" in line.lower() or "it/s]" in line or "s/it]" in line)
    try:
        info = worker.request(cmd="load", model_id=repo_id)
    except Exception:
        worker.close()
        raise
    return VoxCPMModel(repo_id, worker, info.get("sample_rate", 48000), mode, info.get("v2", False))
