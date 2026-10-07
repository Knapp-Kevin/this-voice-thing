"""Isolated RVC feasibility harness.

This module intentionally does not integrate RVC into the application UI.
It pins and manages a separate upstream checkout/environment so issue #29 can
produce reproducible evidence before product integration is considered.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from this_voice_thing import paths


NAME = "rvc"
UPSTREAM_REPOSITORY = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git"
UPSTREAM_COMMIT = "81eed5e8f68b6bed1789f682fe78cdd324495afc"
UPSTREAM_LICENSE = "MIT"
PYTHON_VERSION = "3.12"
TORCH_VERSION = "2.7.1+cu128"
TORCHAUDIO_VERSION = "2.7.1+cu128"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
PYPI_INDEX = "https://pypi.org/simple"
UPSTREAM_REQUIREMENTS = "requirments_cu128_py312.txt"

ASSET_REPOSITORY = "lj1995/VoiceConversionWebUI"
ASSET_REVISION = "1be9d36ece685661920e1a7cb36eb0437c1e5581"
HUBERT_MODEL_SHA256 = "cc8c20f4b90a520757260197a3ff2505705a7adbd20ad9eeaa4e1a9b38442ef5"
RMVPE_SHA256 = "6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193"

ENGINE_DIR = Path(paths.ENGINES_DIR) / NAME
SOURCE_DIR = ENGINE_DIR / "upstream"
VENV_DIR = ENGINE_DIR / ".venv"
SANITIZED_REQUIREMENTS = ENGINE_DIR / "requirements-this-voice-thing.txt"
SOURCE_MARKER = SOURCE_DIR / ".this-voice-thing-source.json"


def venv_python() -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return VENV_DIR / folder / name


def venv_executable(name: str) -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return VENV_DIR / folder / f"{name}{suffix}"


def sanitize_requirements(text: str) -> str:
    """Strip upstream index directives while preserving package constraints."""
    kept = []
    for raw in str(text or "").splitlines():
        stripped = raw.strip()
        lowered = stripped.lower()
        if lowered.startswith("--index-url") or lowered.startswith("--extra-index-url"):
            continue
        if lowered.startswith(("torch==", "torchaudio==", "torchvision==", "torch-directml==")):
            continue
        kept.append(raw.rstrip())
    return "\n".join(kept).rstrip() + "\n"


def install_commands(uv: str, git: str) -> list[tuple[list[str], Path | None]]:
    python = str(venv_python())
    return [
        ([git, "init", str(SOURCE_DIR)], None),
        ([git, "-C", str(SOURCE_DIR), "remote", "add", "origin", UPSTREAM_REPOSITORY], None),
        (
            [
                git,
                "-C",
                str(SOURCE_DIR),
                "fetch",
                "--depth",
                "1",
                "origin",
                UPSTREAM_COMMIT,
            ],
            None,
        ),
        ([git, "-C", str(SOURCE_DIR), "checkout", "--detach", "FETCH_HEAD"], None),
        (
            [
                uv,
                "venv",
                str(VENV_DIR),
                "--python",
                PYTHON_VERSION,
                "--clear",
            ],
            None,
        ),
        (
            [
                uv,
                "pip",
                "install",
                "--python",
                python,
                f"torch=={TORCH_VERSION}",
                f"torchaudio=={TORCHAUDIO_VERSION}",
                "--index-url",
                TORCH_INDEX,
                "--extra-index-url",
                PYPI_INDEX,
            ],
            None,
        ),
        (
            [
                uv,
                "pip",
                "install",
                "--python",
                python,
                "-r",
                str(SANITIZED_REQUIREMENTS),
                "--index-url",
                PYPI_INDEX,
            ],
            None,
        ),
    ]


def build_offline_command(
    *,
    model: str,
    input_path: str,
    output_path: str,
    index_path: str | None = None,
    pitch: int = 0,
    formant: float = 0.0,
    f0_method: str = "rmvpe",
    index_rate: float = 0.0,
    speaker_id: int | None = None,
) -> list[str]:
    if f0_method not in {"rmvpe", "pm"}:
        raise ValueError("f0_method must be 'rmvpe' or 'pm' for the pinned RVC CLI.")
    command = [
        str(venv_python()),
        str(SOURCE_DIR / "infer" / "cli.py"),
        "--model",
        str(Path(model).resolve()),
        "--input",
        str(Path(input_path).resolve()),
        "--output",
        str(Path(output_path).resolve()),
        "--pitch",
        str(int(pitch)),
        "--f0-method",
        f0_method,
        "--index-rate",
        str(float(index_rate)),
        "--overwrite",
    ]
    if index_path:
        command += ["--index", str(Path(index_path).resolve())]
    if speaker_id is not None:
        command += ["--speaker-id", str(int(speaker_id))]
    # Current offline CLI does not expose realtime formant shift. Keep the
    # argument in the harness API so the feasibility report records this
    # distinction, but fail rather than pretending it was applied.
    if abs(float(formant)) > 1e-9:
        raise ValueError(
            "The pinned RVC offline CLI does not expose formant shifting; "
            "formant belongs to the realtime worker phase."
        )
    return command


def _run(command: list[str], *, cwd: Path | None = None, log=print) -> None:
    log("Running: " + " ".join(str(part) for part in command[:6]) + (" ..." if len(command) > 6 else ""))
    process = subprocess.Popen(
        [str(part) for part in command],
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.stdout is not None
    for line in process.stdout:
        log(line.rstrip())
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"Command failed with exit code {code}: {command[0]}")


def _source_revision(git: str | None = None) -> str | None:
    git = git or shutil.which("git")
    if not git or not SOURCE_DIR.is_dir():
        return None
    try:
        return subprocess.check_output(
            [git, "-C", str(SOURCE_DIR), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def status() -> dict:
    python = venv_python()
    revision = _source_revision()
    python_version = None
    if python.exists():
        try:
            python_version = subprocess.check_output(
                [str(python), "--version"],
                text=True,
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.STDOUT,
            ).strip()
        except Exception:
            python_version = "unavailable"
    hubert = SOURCE_DIR / "assets" / "hubert_base" / "pytorch_model.bin"
    rmvpe = SOURCE_DIR / "assets" / "rmvpe" / "rmvpe.pt"
    marker_matches = False
    if SOURCE_MARKER.is_file():
        try:
            marker = json.loads(SOURCE_MARKER.read_text(encoding="utf-8"))
            marker_matches = marker.get("commit") == UPSTREAM_COMMIT
        except Exception:
            marker_matches = False
    return {
        "engine": NAME,
        "upstream_repository": UPSTREAM_REPOSITORY,
        "expected_revision": UPSTREAM_COMMIT,
        "source_revision": revision,
        "source_matches_pin": revision == UPSTREAM_COMMIT,
        "source_marker_matches": marker_matches,
        "upstream_license": UPSTREAM_LICENSE,
        "asset_repository": ASSET_REPOSITORY,
        "asset_revision": ASSET_REVISION,
        "hubert_exists": hubert.is_file(),
        "rmvpe_exists": rmvpe.is_file(),
        "shared_assets_ready": bool(hubert.is_file() and rmvpe.is_file()),
        "python": str(python),
        "python_exists": python.exists(),
        "python_version": python_version,
        "installed": bool(
            python.exists()
            and revision == UPSTREAM_COMMIT
            and marker_matches
        ),
        "source_dir": str(SOURCE_DIR),
        "venv_dir": str(VENV_DIR),
    }


def install(*, reset: bool = False, log=print) -> dict:
    uv = shutil.which("uv")
    git = shutil.which("git")
    if not uv:
        raise RuntimeError("uv was not found on PATH.")
    if not git:
        raise RuntimeError("git was not found on PATH.")

    ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    current = _source_revision(git)
    if SOURCE_DIR.exists() and current != UPSTREAM_COMMIT:
        if not reset:
            raise RuntimeError(
                "RVC source directory exists at a different revision. "
                "Re-run with reset=True to replace it."
            )
        shutil.rmtree(SOURCE_DIR)

    if not SOURCE_DIR.exists():
        # The requirements file is available only after the pinned source checkout.
        for command, cwd in install_commands(uv, git)[:4]:
            _run(command, cwd=cwd, log=log)

    requirement_path = SOURCE_DIR / UPSTREAM_REQUIREMENTS
    if not requirement_path.is_file():
        raise RuntimeError(f"Pinned RVC requirements file is missing: {requirement_path}")
    SOURCE_MARKER.write_text(
        json.dumps(
            {
                "repository": UPSTREAM_REPOSITORY,
                "commit": UPSTREAM_COMMIT,
                "license": UPSTREAM_LICENSE,
            },
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    SANITIZED_REQUIREMENTS.write_text(
        sanitize_requirements(requirement_path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )

    # Environment creation/install is intentionally repeatable. uv reuses its cache.
    for command, cwd in install_commands(uv, git)[4:]:
        _run(command, cwd=cwd, log=log)

    result = status()
    if not result["installed"]:
        raise RuntimeError("RVC feasibility environment did not reach the pinned installed state.")
    return result


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.lower() != str(expected).lower():
        raise RuntimeError(
            f"Asset checksum mismatch for {path}: expected {expected}, got {actual}"
        )


def download_shared_assets(log=print) -> dict:
    if not status()["installed"]:
        raise RuntimeError("Install the pinned RVC feasibility environment first.")
    script = r"""
from huggingface_hub import hf_hub_download, snapshot_download
root = r""" + json.dumps(str(SOURCE_DIR)) + r"""
snapshot_download(
    repo_id=""" + json.dumps(ASSET_REPOSITORY) + r""",
    revision=""" + json.dumps(ASSET_REVISION) + r""",
    allow_patterns=["hubert_base/*"],
    local_dir=root + "/assets",
)
hf_hub_download(
    repo_id=""" + json.dumps(ASSET_REPOSITORY) + r""",
    filename="rmvpe.pt",
    revision=""" + json.dumps(ASSET_REVISION) + r""",
    local_dir=root + "/assets/rmvpe",
)
"""
    _run([str(venv_python()), "-c", script], cwd=SOURCE_DIR, log=log)
    hubert = SOURCE_DIR / "assets" / "hubert_base" / "pytorch_model.bin"
    rmvpe = SOURCE_DIR / "assets" / "rmvpe" / "rmvpe.pt"
    if not hubert.is_file() or not rmvpe.is_file():
        raise RuntimeError("Pinned RVC shared assets were not downloaded completely.")
    _verify_sha256(hubert, HUBERT_MODEL_SHA256)
    _verify_sha256(rmvpe, RMVPE_SHA256)
    return {
        "asset_repository": ASSET_REPOSITORY,
        "asset_revision": ASSET_REVISION,
        "hubert": str(hubert.parent),
        "hubert_sha256": HUBERT_MODEL_SHA256,
        "rmvpe": str(rmvpe),
        "rmvpe_sha256": RMVPE_SHA256,
    }


def offline_convert(
    *,
    model: str,
    input_path: str,
    output_path: str,
    index_path: str | None = None,
    pitch: float = 0.0,
    f0_method: str = "rmvpe",
    index_rate: float = 0.0,
    speaker_id: int | None = None,
    log=print,
) -> dict:
    current = status()
    if not current["installed"]:
        raise RuntimeError("Install the pinned RVC feasibility environment first.")

    for label, candidate in (("model", model), ("input", input_path)):
        if not Path(candidate).is_file():
            raise FileNotFoundError(f"RVC {label} file not found: {candidate}")
    if index_path and not Path(index_path).is_file():
        raise FileNotFoundError(f"RVC index file not found: {index_path}")

    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_offline_command(
        model=model,
        input_path=input_path,
        output_path=str(output),
        index_path=index_path,
        pitch=pitch,
        f0_method=f0_method,
        index_rate=index_rate,
        speaker_id=speaker_id,
    )
    started = time.perf_counter()
    _run(command, cwd=SOURCE_DIR, log=log)
    elapsed = time.perf_counter() - started
    if not output.is_file():
        raise RuntimeError("RVC CLI exited successfully but did not create the requested output.")
    return {
        "output": str(output),
        "elapsed_seconds": round(elapsed, 4),
        "bytes": output.stat().st_size,
        "upstream_revision": UPSTREAM_COMMIT,
        "f0_method": f0_method,
        "pitch": int(pitch),
        "index_rate": float(index_rate),
    }
