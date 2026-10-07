"""Realtime RVC prototype facade.

The canonical feasibility harness owns source checkout, environment creation,
shared-asset pinning/checksums, and offline smoke conversion. This module layers
only the realtime worker and shared AudioFrame adapter on top of that harness.

It remains experimental issue #29 infrastructure and is not registered as a
normal user-facing model backend.
"""

from __future__ import annotations

import base64
from pathlib import Path

from this_voice_thing.core.live_voice import AudioFrame
from this_voice_thing.engines import rvc_feasibility as feasibility
from this_voice_thing.engines import worker as engine_worker

NAME = feasibility.NAME
UPSTREAM_REPO = feasibility.UPSTREAM_REPOSITORY
UPSTREAM_COMMIT = feasibility.UPSTREAM_COMMIT
PYTHON_VERSION = feasibility.PYTHON_VERSION
TORCH_VERSION = feasibility.TORCH_VERSION
TORCHAUDIO_VERSION = feasibility.TORCHAUDIO_VERSION
TORCH_INDEX = feasibility.TORCH_INDEX
PYPI_INDEX = feasibility.PYPI_INDEX
ASSET_REPO = feasibility.ASSET_REPOSITORY
ASSET_REVISION = feasibility.ASSET_REVISION

ROOT = feasibility.ENGINE_DIR
PYTHON = feasibility.venv_python()
WORKER = ROOT / "rvc_worker.py"
UPSTREAM = feasibility.SOURCE_DIR

# Backward-compatible name used by existing prototype tests.
sanitize_upstream_requirements = feasibility.sanitize_requirements


def required_upstream_files(root=UPSTREAM):
    root = Path(root)
    return (
        root / "configs" / "config.py",
        root / "infer" / "rtrvc.py",
        root / "tools" / "cuda_graph.py",
        root / "RVCRealtimeVST" / "worker" / "rvc_worker.py",
        root / feasibility.UPSTREAM_REQUIREMENTS,
        root / ".this-voice-thing-source.json",
    )


def required_runtime_assets(root=UPSTREAM):
    root = Path(root)
    return (
        root / "assets" / "hubert_base" / "pytorch_model.bin",
        root / "assets" / "rmvpe" / "rmvpe.pt",
    )


def is_installed():
    status = feasibility.status()
    return bool(
        status.get("installed")
        and status.get("shared_assets_ready")
        and WORKER.is_file()
        and all(path.is_file() for path in required_upstream_files())
        and all(path.is_file() for path in required_runtime_assets())
    )


def install(log=print):
    """Install/repair the canonical harness and required realtime assets."""
    environment = feasibility.install(log=log)
    assets = None
    if not environment.get("shared_assets_ready"):
        assets = feasibility.download_shared_assets(log=log)
    final = feasibility.status()
    if not final.get("installed") or not final.get("shared_assets_ready"):
        raise RuntimeError("RVC feasibility environment/assets did not reach a ready state.")
    return {
        "engine": NAME,
        "prototype": True,
        "upstream_repo": UPSTREAM_REPO,
        "upstream_commit": UPSTREAM_COMMIT,
        "upstream_code_license": feasibility.UPSTREAM_LICENSE,
        "asset_repo": ASSET_REPO,
        "asset_revision": ASSET_REVISION,
        "python": PYTHON_VERSION,
        "torch": TORCH_VERSION,
        "torchaudio": TORCHAUDIO_VERSION,
        "cuda_profile": "cu128",
        "bundled_target_voice_model": False,
        "environment": final,
        "assets": assets,
    }


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

    def reset_stream_state(self):
        if not self.block_frames:
            raise RuntimeError("Load an RVC model before resetting stream state.")
        return self.worker.request(cmd="reset")

    def close(self):
        self.worker.close()


class RVCFrameAdapter:
    """Accumulate shared AudioFrame PCM into exact RVC blocks.

    This adapter is intentionally UI-free. It proves that the shared microphone
    source contract can feed the isolated RVC worker without giving RVC device or
    routing ownership.
    """

    def __init__(
        self,
        prototype,
        *,
        pitch=0.0,
        formant=0.0,
        index_rate=0.0,
        rms_mix=0.5,
        threshold=-60.0,
        f0_method="rmvpe",
    ):
        if not getattr(prototype, "block_frames", None):
            raise RuntimeError("Load an RVC model before creating the frame adapter.")
        if not getattr(prototype, "sample_rate", None):
            raise RuntimeError("Loaded RVC prototype does not expose a sample rate.")
        self.prototype = prototype
        self.sample_rate = int(prototype.sample_rate)
        self.block_frames = int(prototype.block_frames)
        self.block_bytes = self.block_frames * 2
        self.pitch = float(pitch)
        self.formant = float(formant)
        self.index_rate = float(index_rate)
        self.rms_mix = float(rms_mix)
        self.threshold = float(threshold)
        self.f0_method = str(f0_method)
        self._pending = bytearray()
        self._next_discontinuity = False
        self.blocks = 0
        self.input_bytes = 0
        self.output_bytes = 0
        self.discontinuities = 0
        self.deadline_misses = 0
        self.max_deadline_ratio = 0.0

    def reset_discontinuity(self):
        self._pending.clear()
        self.prototype.reset_stream_state()
        self._next_discontinuity = True
        self.discontinuities += 1

    def process_frame(self, frame):
        if frame.sample_format != "s16le":
            raise ValueError("RVC frame adapter requires signed-16 little-endian PCM.")
        if int(frame.channels) != 1:
            raise ValueError("RVC frame adapter requires mono PCM.")
        if int(frame.sample_rate) != self.sample_rate:
            raise ValueError(
                f"RVC frame sample rate must be {self.sample_rate} Hz; "
                f"received {frame.sample_rate} Hz."
            )
        if frame.discontinuity:
            self.reset_discontinuity()

        raw = bytes(frame.pcm or b"")
        self.input_bytes += len(raw)
        self._pending.extend(raw)
        output = []

        while len(self._pending) >= self.block_bytes:
            block = bytes(self._pending[: self.block_bytes])
            del self._pending[: self.block_bytes]
            converted = self.prototype.process_pcm(
                block,
                pitch=self.pitch,
                formant=self.formant,
                index_rate=self.index_rate,
                rms_mix=self.rms_mix,
                threshold=self.threshold,
                f0_method=self.f0_method,
            )
            if len(converted) != self.block_bytes:
                raise RuntimeError(
                    "RVC worker returned a block with an unexpected PCM size."
                )
            metrics = dict(getattr(self.prototype, "last_metrics", {}) or {})
            ratio = metrics.get("deadline_ratio")
            if ratio is not None:
                ratio = float(ratio)
                self.max_deadline_ratio = max(self.max_deadline_ratio, ratio)
                if ratio > 1.0:
                    self.deadline_misses += 1

            discontinuity = bool(self._next_discontinuity)
            self._next_discontinuity = False
            self.blocks += 1
            self.output_bytes += len(converted)
            output.append(
                AudioFrame(
                    pcm=bytes(converted),
                    sample_rate=self.sample_rate,
                    channels=1,
                    sample_format="s16le",
                    discontinuity=discontinuity,
                    provenance="rvc-neural-conversion",
                )
            )
        return output

    def flush_pending(self):
        """Discard incomplete input at end/Stop; realtime RVC only accepts full blocks."""
        discarded = len(self._pending)
        self._pending.clear()
        return discarded

    def metrics(self):
        return {
            "mode": "rvc-prototype",
            "sample_rate": self.sample_rate,
            "block_frames": self.block_frames,
            "block_ms": round(1000.0 * self.block_frames / self.sample_rate, 3),
            "blocks": int(self.blocks),
            "input_bytes": int(self.input_bytes),
            "output_bytes": int(self.output_bytes),
            "pending_bytes": len(self._pending),
            "discontinuities": int(self.discontinuities),
            "deadline_misses": int(self.deadline_misses),
            "max_deadline_ratio": round(float(self.max_deadline_ratio), 4),
            "provenance": "rvc-neural-conversion",
        }
