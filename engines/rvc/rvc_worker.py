"""Isolated RVC realtime inference worker for This Voice Thing.

Runs inside engines/rvc/.venv (Python 3.12) and imports a pinned RVC upstream
checkout. The worker never opens audio devices. This Voice Thing owns capture,
routing, monitoring, and Stop semantics.

Protocol: one JSON request per line on stdin, one JSON reply per line on stdout.
Library output is redirected to stderr.

Commands:
  {"cmd":"probe","rvc_root":"..."}
  {"cmd":"load","rvc_root":"...","model_path":"...","index_path":"",
   "sample_rate":48000,"block_ms":250,"crossfade_ms":50,"extra_ms":2500}
  {"cmd":"process","data":"<base64 s16le>","pitch":0,"formant":0,
   "index_rate":0,"rms_mix":0.5,"threshold":-60,"f0_method":"rmvpe"}
  {"cmd":"ping"}
  {"cmd":"shutdown"}
"""

import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback

REPLY = sys.stdout
sys.stdout = sys.stderr

UPSTREAM_COMMIT = "81eed5e8f68b6bed1789f682fe78cdd324495afc"


def reply(**payload):
    REPLY.write(json.dumps(payload, ensure_ascii=False) + "\n")
    REPLY.flush()


def required_upstream_files(root):
    root = Path(root)
    return [
        root / "configs" / "config.py",
        root / "infer" / "rtrvc.py",
        root / "tools" / "cuda_graph.py",
        root / "RVCRealtimeVST" / "worker" / "rvc_worker.py",
        root / ".this-voice-thing-source.json",
    ]


def validate_upstream(root):
    root = Path(root).resolve()
    missing = [str(path.relative_to(root)) for path in required_upstream_files(root) if not path.is_file()]
    if missing:
        raise RuntimeError("Pinned RVC checkout is incomplete; missing: " + ", ".join(missing))
    marker = json.loads((root / ".this-voice-thing-source.json").read_text(encoding="utf-8"))
    if marker.get("commit") != UPSTREAM_COMMIT:
        raise RuntimeError(
            "RVC source revision mismatch: "
            f"{marker.get('commit') or 'unknown'} != {UPSTREAM_COMMIT}"
        )
    return root


def load_stream_engine_class(root):
    root = validate_upstream(root)
    module_path = root / "RVCRealtimeVST" / "worker" / "rvc_worker.py"
    spec = importlib.util.spec_from_file_location("tvt_rvc_upstream_worker", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import upstream RVC worker: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RVCStreamEngine


def pcm16_to_float(data, expected_frames):
    import numpy as np

    raw = base64.b64decode(data, validate=True)
    if len(raw) % 2:
        raise ValueError("RVC input PCM must contain complete signed-16 samples.")
    samples = np.frombuffer(raw, dtype="<i2")
    if len(samples) != int(expected_frames):
        raise ValueError(
            f"RVC block mismatch: worker={expected_frames} frames, request={len(samples)} frames."
        )
    return samples.astype(np.float32) / 32768.0


def float_to_pcm16(values):
    import numpy as np

    values = np.asarray(values, dtype=np.float32).reshape(-1)
    return (
        np.clip(values, -1.0, 1.0) * 32767.0
    ).astype("<i2", copy=False).tobytes()


def main():
    state = {
        "engine": None,
        "block_frames": 0,
        "sample_rate": 0,
        "root": None,
        "model_path": None,
    }

    reply(
        ok=True,
        event="ready",
        python=sys.version.split()[0],
        upstream_commit=UPSTREAM_COMMIT,
        cuda=False,
        device="unloaded",
    )

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")

            if cmd == "probe":
                root = validate_upstream(req["rvc_root"])
                reply(
                    ok=True,
                    upstream_root=str(root),
                    upstream_commit=UPSTREAM_COMMIT,
                    required_files=[str(path.relative_to(root)) for path in required_upstream_files(root)],
                )

            elif cmd == "load":
                root = validate_upstream(req["rvc_root"])
                model_path = Path(req["model_path"]).expanduser().resolve()
                if not model_path.is_file():
                    raise FileNotFoundError(f"RVC model not found: {model_path}")
                index_value = str(req.get("index_path") or "").strip()
                index_path = Path(index_value).expanduser().resolve() if index_value else None
                if index_path is not None and not index_path.is_file():
                    raise FileNotFoundError(f"RVC index not found: {index_path}")

                # RVC Config parses process argv intended for its WebUI.
                sys.argv = [sys.argv[0]]
                os.chdir(root)
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))

                engine_class = load_stream_engine_class(root)
                cfg = {
                    "rvc_root": str(root),
                    "model_path": str(model_path),
                    "index_path": str(index_path) if index_path else "",
                    "sample_rate": int(req.get("sample_rate", 48000)),
                    "block_ms": float(req.get("block_ms", 250.0)),
                    "crossfade_ms": float(req.get("crossfade_ms", 50.0)),
                    "extra_ms": float(req.get("extra_ms", 2500.0)),
                }
                started = time.perf_counter()
                engine = engine_class(cfg)
                engine.prewarm()
                load_seconds = time.perf_counter() - started

                state.update(
                    engine=engine,
                    block_frames=int(engine.block_frame),
                    sample_rate=int(engine.sample_rate),
                    root=str(root),
                    model_path=str(model_path),
                )
                torch = engine.torch
                device = str(engine.config.device)
                cuda = torch.device(engine.config.device).type == "cuda"
                reply(
                    ok=True,
                    event="loaded",
                    sample_rate=state["sample_rate"],
                    block_frames=state["block_frames"],
                    block_ms=round(1000.0 * state["block_frames"] / state["sample_rate"], 3),
                    effective_crossfade_ms=round(float(engine.effective_crossfade_ms), 3),
                    load_seconds=round(load_seconds, 4),
                    cuda=cuda,
                    device=device,
                    gpu=(torch.cuda.get_device_name(0) if cuda else None),
                )

            elif cmd == "process":
                engine = state["engine"]
                if engine is None:
                    raise RuntimeError("No RVC model is loaded.")
                audio = pcm16_to_float(req["data"], state["block_frames"])
                methods = {"rmvpe": 0, "fcpe": 1, "pm": 2}
                method = str(req.get("f0_method", "rmvpe")).lower()
                if method not in methods:
                    raise ValueError("f0_method must be rmvpe, fcpe, or pm.")

                started = time.perf_counter()
                cpu_started = time.process_time()
                converted = engine.process(
                    audio,
                    float(req.get("pitch", 0.0)),
                    float(req.get("formant", 0.0)),
                    float(req.get("index_rate", 0.0)),
                    float(req.get("rms_mix", 0.5)),
                    float(req.get("threshold", -60.0)),
                    methods[method],
                )
                inference_ms = (time.perf_counter() - started) * 1000.0
                cpu_ms = (time.process_time() - cpu_started) * 1000.0
                deadline_ms = 1000.0 * state["block_frames"] / state["sample_rate"]
                torch = engine.torch
                cuda = torch.device(engine.config.device).type == "cuda"
                vram_allocated_mb = (
                    torch.cuda.memory_allocated(engine.config.device) / (1024 * 1024)
                    if cuda else None
                )
                vram_reserved_mb = (
                    torch.cuda.memory_reserved(engine.config.device) / (1024 * 1024)
                    if cuda else None
                )
                vram_peak_mb = (
                    torch.cuda.max_memory_allocated(engine.config.device) / (1024 * 1024)
                    if cuda else None
                )
                raw = float_to_pcm16(converted)
                reply(
                    ok=True,
                    event="audio",
                    data=base64.b64encode(raw).decode("ascii"),
                    samples=state["block_frames"],
                    sample_rate=state["sample_rate"],
                    inference_ms=round(inference_ms, 3),
                    cpu_ms=round(cpu_ms, 3),
                    deadline_ms=round(deadline_ms, 3),
                    deadline_ratio=round(inference_ms / deadline_ms, 4) if deadline_ms else None,
                    vram_allocated_mb=round(vram_allocated_mb, 2) if vram_allocated_mb is not None else None,
                    vram_reserved_mb=round(vram_reserved_mb, 2) if vram_reserved_mb is not None else None,
                    vram_peak_mb=round(vram_peak_mb, 2) if vram_peak_mb is not None else None,
                )

            elif cmd == "ping":
                reply(
                    ok=True,
                    loaded=state["engine"] is not None,
                    sample_rate=state["sample_rate"] or None,
                    block_frames=state["block_frames"] or None,
                )

            elif cmd == "shutdown":
                reply(ok=True)
                break

            else:
                reply(ok=False, error=f"Unknown command: {cmd}")

        except Exception as exc:
            traceback.print_exc()
            reply(ok=False, event="error", error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        reply(ok=False, event="fatal", error=f"{type(exc).__name__}: {exc}")
