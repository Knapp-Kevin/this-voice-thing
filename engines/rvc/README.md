# RVC prototype engine

Status: **experimental feasibility prototype for issue #29**.

This directory is not a user-facing Voice Changer engine yet. It exists to
measure whether current RVC realtime inference is fast, stable, and maintainable
enough to promote into the Live Voice product architecture.

## Boundary

This Voice Thing owns:

- microphone capture;
- audio-device selection;
- route arming;
- Local / Discord / Zoom / OBS / virtual-microphone output;
- monitoring;
- Stop semantics;
- diagnostics.

The RVC worker owns only:

- loading a user-provided RVC target model;
- fixed-size PCM block inference;
- RVC pitch/formant/index/RMS/F0 parameters;
- RVC's internal resampling and SOLA/crossfade state.

The main-process `RVCFrameAdapter` bridges the shared Live Voice `AudioFrame`
contract to those exact neural blocks. It accepts ordinary short microphone
frames, buffers only until one complete RVC block is available, and emits
converted `AudioFrame` objects for the existing router.

No ASR, text generation, or TTS occurs in this path.

## Pinned upstream

Source:

- repository: `RVC-Project/Retrieval-based-Voice-Conversion-WebUI`
- commit: `81eed5e8f68b6bed1789f682fe78cdd324495afc`
- code license: MIT

The prototype imports the upstream realtime engine from:

- `infer/rtrvc.py`
- `configs/config.py`
- `tools/cuda_graph.py`
- `RVCRealtimeVST/worker/rvc_worker.py::RVCStreamEngine`

It does **not** use upstream's GUI, sounddevice stream, VST plug-in, shared-memory
transport, or target-app device management.

## Environment

Current target profile:

- Python 3.12
- Torch 2.7.1 + CUDA 12.8
- Torchaudio 2.7.1 + CUDA 12.8
- RTX 50-series target hardware

This intentionally differs from This Voice Thing's normal optional-engine
Python 3.11 / Torch 2.8 convention. The prototype therefore gets its own
`engines/rvc/.venv`.

CPU, DirectML, and pre-RTX50 profiles are not part of this first benchmark
slice. They should only be added if the target-machine result earns product
integration.

## Runtime assets

RVC also requires its shared HuBERT and RMVPE assets.

The canonical feasibility harness in
`this_voice_thing/engines/rvc_feasibility.py` owns this setup and pins:

- asset repository: `lj1995/VoiceConversionWebUI`
- asset revision: `1be9d36ece685661920e1a7cb36eb0437c1e5581`
- HuBERT model SHA-256:
  `cc8c20f4b90a520757260197a3ff2505705a7adbd20ad9eeaa4e1a9b38442ef5`
- RMVPE SHA-256:
  `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193`

The realtime prototype reuses that exact environment and asset set instead of
maintaining a second installer/provenance path.

## Target voice models

This Voice Thing does **not** bundle an RVC target voice model or index.

The benchmark requires a user-supplied:

- `.pth` target model;
- optional matching `.index` file.

The RVC source license does not determine the license of arbitrary trained
target models. Record model/weight provenance separately before redistribution.

For a benchmark-only fallback when no user-owned model is available, issue #29
records `licyk/rvc-model` as the preferred source because its model card says
its demo voices are copied unchanged from the official RVC distributions and
it provides provenance metadata. Treat those demo voices as research benchmark
fixtures, not bundled/product voices.


## Install

From the repo root:

```bat
.venv\Scripts\python.exe scripts\benchmark_rvc.py --install
```

The command delegates environment/source/asset setup to the already-merged
feasibility harness:

1. checks out the exact RVC source commit with Git;
2. writes the immutable source marker used by the realtime worker;
3. creates `engines/rvc/.venv` with Python 3.12 through `uv`;
4. installs the verified CUDA 12.8 Torch/Torchaudio pair first;
5. strips upstream mirror directives **and Torch-family lines** from the second
   requirements stage so that stage cannot silently replace the verified pair;
6. installs remaining upstream dependencies from official PyPI;
7. downloads the fixed HuBERT/RMVPE asset revision and verifies both checksums.

The offline harness remains available separately:

```bat
python scripts\rvc_feasibility.py status
python scripts\rvc_feasibility.py install
python scripts\rvc_feasibility.py assets
```

See `docs/rvc-feasibility.md` for the Phase 1 evidence contract.

The setup is intentionally substantial. RVC is not smuggled into the main app
environment merely because dependency conflicts are emotionally inconvenient.

## Offline block benchmark

Use a clean spoken source clip, not music:

```bat
.venv\Scripts\python.exe scripts\benchmark_rvc.py ^
  --model C:\voices\target.pth ^
  --index C:\voices\target.index ^
  --model-source "your model source / provenance URL" ^
  --model-license "your model license" ^
  --input C:\clips\speech.wav ^
  --output C:\clips\converted.wav ^
  --report C:\clips\rvc-benchmark.json
```

The benchmark feeds short microphone-like `AudioFrame` chunks (20 ms by
default) through `RVCFrameAdapter`, rather than bypassing the adapter with
perfectly aligned RVC-sized input blocks. The final partial input is padded only
for the last neural block and trimmed back to the original source length.

The benchmark reports:

- source `AudioFrame` cadence;
- adapter pending/discontinuity/deadline metrics;
- block size/deadline;
- inference time median / p95 / max;
- deadline ratio median / p95 / max;
- number of blocks that missed their playback deadline;
- whole-file wall-clock RTF;
- model load/prewarm timing;
- worker CPU-time distribution;
- Torch VRAM allocated/reserved/peak values when CUDA is active;
- target model/index SHA-256 plus declared source/license;
- input clip SHA-256;
- exact upstream revision and settings.

The JSON report is intended to be attached to issue #29 as the benchmark evidence record. GPU utilization percentage is still a target-machine observation (for example via Task Manager or `nvidia-smi`) because polling it inside every inference block would distort the timing being measured.

A deadline ratio below `1.0` means inference finished before that audio block
would finish playing. Sustained realtime operation needs margin below 1.0, not
a heroic last-millisecond tie.

## Worker protocol

The worker speaks JSON lines over stdin/stdout.

Commands:

- `probe`
- `load`
- `process`
- `reset`
- `ping`
- `shutdown`

`process` receives one exact-size mono signed-16 PCM block and returns one
same-size block plus inference timing.

`reset` clears the temporal input/resample/RMS/SOLA buffers and pitch caches
without reloading model weights. The frame adapter uses it when the shared
microphone source marks a capture discontinuity, so RVC never crossfades across
audio that was deliberately dropped to keep latency bounded.

Audio devices never cross this boundary.

## Promotion gate

This prototype does not become normal Live Voice UI merely because it runs.

Promotion requires:

- #29 GO recommendation;
- target-machine latency/utilization/dropout evidence;
- #30 shared microphone source semantics (**implemented**);
- #13 validated route/monitor/Stop foundation;
- acceptable target-model licensing/provenance.
