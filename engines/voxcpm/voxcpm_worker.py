"""VoxCPM worker: runs inside engines/voxcpm/.venv and serves the main app.

Protocol: one JSON request per line on stdin. Ordinary requests receive one JSON
reply. Streaming requests receive a sequence of JSON-line events ending in a
done event. Everything libraries print goes to stderr (the app's Log page).

Requests:
  {"cmd": "load", "model_id": "openbmb/VoxCPM2"}
  {"cmd": "generate", "text": ..., "out_path": path, "style": "...",
   "reference_wav": path | null, "prompt_wav": path | null, "prompt_text": str | null,
   "seed": int, "cfg_value": 2.0, "timesteps": 10}
  {"cmd": "generate_stream", ...same synthesis fields...}
  {"cmd": "ping"} / {"cmd": "shutdown"}

Streaming audio events carry base64-encoded model-native mono signed 16-bit
little-endian PCM. A style or voice description goes in parentheses before the
text, which is how VoxCPM2 takes voice design and style control.
"""

import base64
import json
import sys
import time
import traceback
import warnings

REPLY = sys.stdout
sys.stdout = sys.stderr
warnings.filterwarnings("ignore")


def reply(**payload):
    REPLY.write(json.dumps(payload) + "\n")
    REPLY.flush()


def main():
    import numpy as np
    import soundfile as sf
    import torch
    from huggingface_hub import snapshot_download
    from voxcpm import VoxCPM

    cuda = torch.cuda.is_available()
    state = {"model": None, "model_id": None}

    def load(model_id):
        if state["model_id"] == model_id:
            return
        state.update(model=None, model_id=None)
        if cuda:
            torch.cuda.empty_cache()
        # Use the local copy when it's there so loading works offline.
        try:
            source = snapshot_download(model_id, local_files_only=True)
        except Exception:
            source = snapshot_download(model_id)
        # The optional denoiser downloads a separate ModelScope model; it isn't needed
        # for clean reference clips. optimize (torch.compile) needs Triton, which Windows
        # lacks: it gave no speed-up on an RTX 5070 Ti and added ~9 s to loading.
        model = VoxCPM.from_pretrained(source, load_denoiser=False, optimize=False)
        state.update(model=model, model_id=model_id,
                     v2=type(model.tts_model).__name__ == "VoxCPM2Model")
        # The first generation pays a one-time GPU warm-up (~15 s); do it while loading.
        model.tts_model.generate(target_text="Hello, this is a warm-up sentence.", max_len=10)

    def generation_kwargs(req):
        model = state["model"]
        if model is None:
            raise RuntimeError("No VoxCPM model is loaded.")
        if req.get("seed"):
            torch.manual_seed(int(req["seed"]))
            if cuda:
                torch.cuda.manual_seed_all(int(req["seed"]))
        text = req["text"].strip()
        style = (req.get("style") or "").strip().strip("()")
        if style:
            text = f"({style}){text}"
        kwargs = dict(text=text, cfg_value=float(req.get("cfg_value", 2.0)),
                      inference_timesteps=int(req.get("timesteps", 10)))
        if req.get("prompt_wav") and req.get("prompt_text"):
            kwargs.update(prompt_wav_path=req["prompt_wav"], prompt_text=req["prompt_text"])
        if req.get("reference_wav"):
            if state["v2"]:
                kwargs["reference_wav_path"] = req["reference_wav"]
            elif "prompt_wav_path" not in kwargs:
                raise ValueError("This older VoxCPM model clones only with a transcript of the clip. "
                                 "Fill in Clip transcript, or use VoxCPM2.")
        if style and not state["v2"]:
            raise ValueError("Styles and voice design need VoxCPM2; this is an older VoxCPM model.")
        return model, kwargs, int(model.tts_model.sample_rate)

    def generate(req):
        model, kwargs, sr = generation_kwargs(req)
        wav = model.generate(**kwargs)
        wav = np.asarray(wav, dtype=np.float32).reshape(-1)
        sf.write(req["out_path"], wav, sr, subtype="FLOAT")
        return {"path": req["out_path"], "sr": sr, "seconds": round(len(wav) / sr, 2)}

    def pcm16(wav):
        wav = np.asarray(wav, dtype=np.float32).reshape(-1)
        if not len(wav):
            return b""
        return (np.clip(wav, -1.0, 1.0) * 32767.0).astype("<i2", copy=False).tobytes()

    def generate_stream(req):
        model, kwargs, sr = generation_kwargs(req)
        if not state.get("v2"):
            raise ValueError("Native PCM streaming is available only for VoxCPM2.")
        started = time.monotonic()
        first_audio_at = None
        total_samples = 0
        chunks = 0
        reply(ok=True, event="start", sample_rate=sr, format="s16le", channels=1)
        for wav in model.generate_streaming(**kwargs):
            wav = np.asarray(wav, dtype=np.float32).reshape(-1)
            if not len(wav):
                continue
            if first_audio_at is None:
                first_audio_at = time.monotonic()
            data = pcm16(wav)
            total_samples += len(wav)
            chunks += 1
            reply(ok=True, event="audio", data=base64.b64encode(data).decode("ascii"),
                  samples=len(wav))
        ended = time.monotonic()
        reply(ok=True, event="done", sample_rate=sr, format="s16le", channels=1,
              samples=total_samples, chunks=chunks,
              seconds=round(total_samples / sr, 4),
              generation_seconds=round(ended - started, 4),
              ttfa_seconds=round((first_audio_at - started), 4) if first_audio_at else None)

    reply(ok=True, event="ready", cuda=cuda, device=torch.cuda.get_device_name(0) if cuda else "cpu")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "load":
                load(req["model_id"])
                reply(ok=True, sample_rate=int(state["model"].tts_model.sample_rate), v2=state["v2"])
            elif cmd == "generate":
                reply(ok=True, **generate(req))
            elif cmd == "generate_stream":
                generate_stream(req)
            elif cmd == "ping":
                reply(ok=True)
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
