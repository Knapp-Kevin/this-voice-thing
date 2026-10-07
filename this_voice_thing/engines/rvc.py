"""Isolated RVC prototype lifecycle and block-processing facade.

RVC currently targets Python 3.12 and a Torch/CUDA matrix that differs from
This Voice Thing's normal optional engines. It therefore uses a dedicated
installer instead of engines.worker.install_env(), which intentionally targets
the app's Python 3.11 engine convention.

This module does not expose RVC as a normal user-facing model backend yet.
It exists only for issue #29 feasibility/benchmark work.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

from this_voice_thing import paths
from this_voice_thing.engines import worker as engine_worker

NAME = "rvc"
UPSTREAM_REPO = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
UPSTREAM_COMMIT = "81eed5e8f68b6bed1789f682fe78cdd324495afc"
UPSTREAM_ARCHIVE = (
    f"https://github.com/{UPSTREAM_REPO}/archive/{UPSTREAM_COMMIT}.zip"
)
ASSET_REPO = "lj1995/VoiceConversionWebUI"
PYTHON_VERSION = "3.12"
TORCH_VERSION = "2.7.1+cu128"
TORCHAUDIO_VERSION = "2.7.1+cu128"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
PYPI_INDEX = "https://pypi.org/simple"

ROOT = Path(engine_worker.engine_dir(NAME))
PYTHON = Path(engine_worker.venv_python(NAME))
WORKER = ROOT / "rvc_worker.py"
UPSTREAM = ROOT / "upstream"
UPSTREAM_SOURCE_MANIFEST = UPSTREAM / ".this-voice-thing-source.json"
INSTALL_MANIFEST = ROOT / "install.json"
SANITIZED_REQUIREMENTS = ROOT / ".runtime-requirements.txt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def required_upstream_files(root=UPSTREAM):
    root = Path(root)
    return (
        root / "configs" / "config.py",
        root / "infer" / "rtrvc.py",
        root / "tools" / "cuda_graph.py",
        root / "RVCRealtimeVST" / "worker" / "rvc_worker.py",
        root / "requirments_cu128_py312.txt",
        root / ".this-voice-thing-source.json",
    )


def required_runtime_assets(root=UPSTREAM):
    root = Path(root)
    return (
        root / "assets" / "hubert_base" / "config.json",
        root / "assets" / "hubert_base" / "preprocessor_config.json",
        root / "assets" / "hubert_base" / "pytorch_model.bin",
        root / "assets" / "rmvpe" / "rmvpe.pt",
    )


def is_installed():
    if not PYTHON.is_file() or not WORKER.is_file() or not INSTALL_MANIFEST.is_file():
        return False
    if not all(path.is_file() for path in required_upstream_files()):
        return False
    if not all(path.is_file() for path in required_runtime_assets()):
        return False
    try:
        manifest = json.loads(INSTALL_MANIFEST.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        manifest.get("upstream_commit") == UPSTREAM_COMMIT
        and manifest.get("python") == PYTHON_VERSION
        and manifest.get("torch") == TORCH_VERSION
    )


def sanitize_upstream_requirements(text):
    """Remove upstream mirror directives and Torch lines from a pinned file.

    Torch/Torchaudio are installed as an explicit first stage from the official
    CUDA index. Keeping that stage separate prevents later dependency
    resolution from silently replacing the verified CUDA build.
    """
    cleaned = []
    for line in str(text).splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("--index-url") or lowered.startswith("--extra-index-url"):
            continue
        if lowered.startswith(("torch==", "torchaudio==", "torchvision==", "torch-directml==")):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip() + "\n"


def _run(command, log=print, cwd=None):
    command = [str(part) for part in command]
    log("Running: " + " ".join(command[:5]) + (" ..." if len(command) > 5 else ""))
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    for line in process.stdout:
        log(line.rstrip())
    code = process.wait()
    if code != 0:
        raise RuntimeError(
            f"RVC setup command failed ({code}): {' '.join(command[:5])}"
        )


def _download_pinned_upstream(log=print):
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tvt_rvc_") as temp:
        archive = Path(temp) / "rvc.zip"
        log(f"Downloading pinned RVC source {UPSTREAM_COMMIT[:12]}…")
        urllib.request.urlretrieve(UPSTREAM_ARCHIVE, archive)
        extract = Path(temp) / "extract"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extract)
        roots = [path for path in extract.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("Unexpected RVC source archive layout.")
        if UPSTREAM.exists():
            shutil.rmtree(UPSTREAM)
        shutil.move(str(roots[0]), str(UPSTREAM))
    UPSTREAM_SOURCE_MANIFEST.write_text(
        json.dumps(
            {
                "repository": UPSTREAM_REPO,
                "commit": UPSTREAM_COMMIT,
                "archive": UPSTREAM_ARCHIVE,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    missing = [str(path) for path in required_upstream_files() if not path.is_file()]
    if missing:
        raise RuntimeError("Pinned RVC source is incomplete: " + ", ".join(missing))


def _install_runtime_assets(log=print):
    """Resolve the asset repo to an immutable SHA, download required files, return SHA."""
    code = (
        "from huggingface_hub import HfApi,snapshot_download,hf_hub_download;"
        "from pathlib import Path;"
        "import shutil;"
        f"repo={ASSET_REPO!r};"
        "api=HfApi();sha=api.model_info(repo).sha;"
        f"root=Path({str(UPSTREAM)!r});"
        "snapshot_download(repo,revision=sha,allow_patterns=['hubert_base/*'],"
        "local_dir=str(root/'assets'));"
        "p=hf_hub_download(repo,'rmvpe.pt',revision=sha);"
        "(root/'assets'/'rmvpe').mkdir(parents=True,exist_ok=True);"
        "shutil.copy2(p,root/'assets'/'rmvpe'/'rmvpe.pt');"
        "print('ASSET_REVISION='+sha)"
    )
    process = subprocess.Popen(
        [str(PYTHON), "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    revision = None
    for line in process.stdout:
        line = line.rstrip()
        log(line)
        if line.startswith("ASSET_REVISION="):
            revision = line.partition("=")[2].strip()
    if process.wait() != 0 or not revision:
        raise RuntimeError("RVC runtime asset download failed.")
    return revision


def install(log=print):
    """Install the target RTX-50/CUDA-12.8 RVC prototype environment.

    This is intentionally a prototype installer for issue #29, not a general
    hardware chooser. CPU/DirectML/pre-RTX50 profiles remain future work if the
    target-machine benchmark earns product integration.
    """
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv was not found on PATH; it is required to install the RVC prototype.")

    _download_pinned_upstream(log)

    ROOT.mkdir(parents=True, exist_ok=True)
    _run([uv, "venv", str(ROOT / ".venv"), "--python", PYTHON_VERSION, "--clear"], log)
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(PYTHON),
            f"torch=={TORCH_VERSION}",
            f"torchaudio=={TORCHAUDIO_VERSION}",
            "--index-url",
            TORCH_INDEX,
            "--extra-index-url",
            PYPI_INDEX,
        ],
        log,
    )

    upstream_requirements = (
        UPSTREAM / "requirments_cu128_py312.txt"
    ).read_text(encoding="utf-8")
    SANITIZED_REQUIREMENTS.write_text(
        sanitize_upstream_requirements(upstream_requirements),
        encoding="utf-8",
        newline="\n",
    )
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(PYTHON),
            "-r",
            str(SANITIZED_REQUIREMENTS),
            "--index-url",
            PYPI_INDEX,
        ],
        log,
    )

    asset_revision = _install_runtime_assets(log)
    manifest = {
        "engine": NAME,
        "prototype": True,
        "upstream_repo": UPSTREAM_REPO,
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_code_license": "MIT",
        "asset_repo": ASSET_REPO,
        "asset_revision": asset_revision,
        "python": PYTHON_VERSION,
        "torch": TORCH_VERSION,
        "torchaudio": TORCHAUDIO_VERSION,
        "cuda_profile": "cu128",
        "bundled_target_voice_model": False,
    }
    INSTALL_MANIFEST.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


class RVCPrototype:
    """Main-process facade over the isolated block inference worker."""

    def __init__(self, log=print):
        if not is_installed():
            raise RuntimeError(
                "The RVC prototype environment is not installed. Run the RVC prototype setup first."
            )
        self.worker = engine_worker.WorkerProcess(
            str(PYTHON),
            str(WORKER),
            "rvc",
            log,
        )
        self.sample_rate = None
        self.block_frames = None
        self.block_ms = None
        self.last_metrics = {}

    def load(
        self,
        model_path,
        index_path="",
        *,
        sample_rate=48000,
        block_ms=250.0,
        crossfade_ms=50.0,
        extra_ms=2500.0,
    ):
        info = self.worker.request(
            cmd="load",
            rvc_root=str(UPSTREAM),
            model_path=str(Path(model_path).expanduser().resolve()),
            index_path=str(Path(index_path).expanduser().resolve()) if index_path else "",
            sample_rate=int(sample_rate),
            block_ms=float(block_ms),
            crossfade_ms=float(crossfade_ms),
            extra_ms=float(extra_ms),
        )
        self.sample_rate = int(info["sample_rate"])
        self.block_frames = int(info["block_frames"])
        self.block_ms = float(info["block_ms"])
        self.worker.device = str(info.get("device") or self.worker.device)
        return info

    def process_pcm(
        self,
        pcm,
        *,
        pitch=0.0,
        formant=0.0,
        index_rate=0.0,
        rms_mix=0.5,
        threshold=-60.0,
        f0_method="rmvpe",
    ):
        if not self.block_frames:
            raise RuntimeError("Load an RVC model before processing audio.")
        raw = bytes(pcm or b"")
        expected = self.block_frames * 2
        if len(raw) != expected:
            raise ValueError(
                f"RVC block must contain exactly {self.block_frames} mono s16le samples "
                f"({expected} bytes); received {len(raw)} bytes."
            )
        reply = self.worker.request(
            cmd="process",
            data=base64.b64encode(raw).decode("ascii"),
            pitch=float(pitch),
            formant=float(formant),
            index_rate=float(index_rate),
            rms_mix=float(rms_mix),
            threshold=float(threshold),
            f0_method=str(f0_method),
        )
        self.last_metrics = {
            key: reply.get(key)
            for key in (
                "inference_ms",
                "cpu_ms",
                "deadline_ms",
                "deadline_ratio",
                "samples",
                "sample_rate",
                "vram_allocated_mb",
                "vram_reserved_mb",
                "vram_peak_mb",
            )
        }
        return base64.b64decode(reply["data"])

    def close(self):
        self.worker.close()
