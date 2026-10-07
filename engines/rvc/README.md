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

The installer resolves `lj1995/VoiceConversionWebUI` to the repository's
immutable current SHA at install time, downloads only the required assets at
that SHA, and records the resolved revision in `engines/rvc/install.json`.

This avoids silently treating a floating `main` asset revision as
reproducible evidence.

## Target voice models

This Voice Thing does **not** bundle an RVC target voice model or index.

The benchmark requires a user-supplied:

- `.pth` target model;
- optional matching `.index` file.

The RVC source license does not determine the license of arbitrary trained
target models. Record model/weight provenance separately before redistribution.

## Install

From the repo root:

```bat
.venv\Scripts\python.exe scripts\benchmark_rvc.py --install
```

The installer:

1. downloads the exact pinned upstream source archive;
2. creates `engines/rvc/.venv` with Python 3.12 through `uv`;
3. installs the verified CUDA 12.8 Torch/Torchaudio pair first;
4. installs the pinned upstream dependency set from official package indexes;
5. resolves/downloads HuBERT + RMVPE at one immutable asset revision;
6. writes `engines/rvc/install.json`.

The setup is intentionally substantial. RVC is not smuggled into the main app
environment merely because dependency conflicts are emotionally inconvenient.

## Offline block benchmark

Use a clean spoken source clip, not music:

```bat
.venv\Scripts\python.exe scripts\benchmark_rvc.py ^
  --model C:\voices\target.pth ^
  --index C:\voices\target.index ^
  --input C:\clips\speech.wav ^
  --output C:\clips\converted.wav ^
  --report C:\clips\rvc-benchmark.json
```

The benchmark reports:

- block size/deadline;
- inference time median / p95 / max;
- deadline ratio median / p95 / max;
- number of blocks that missed their playback deadline;
- whole-file wall-clock RTF;
- model load/prewarm timing;
- worker CPU-time distribution;
- Torch VRAM allocated/reserved/peak values when CUDA is active;
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
- `ping`
- `shutdown`

`process` receives one exact-size mono signed-16 PCM block and returns one
same-size block plus inference timing. Audio devices never cross this boundary.

## Promotion gate

This prototype does not become normal Live Voice UI merely because it runs.

Promotion requires:

- #29 GO recommendation;
- target-machine latency/utilization/dropout evidence;
- #30 shared microphone source semantics;
- #13 validated route/monitor/Stop foundation;
- acceptable target-model licensing/provenance.
