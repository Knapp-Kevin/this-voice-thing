# RVC Feasibility Harness

Status: isolated research/prototype support for issue #29  
Product integration: **not enabled**

This harness exists to make the RVC decision reproducible before This Voice Thing exposes neural microphone conversion in the UI.

## Pinned upstream

- Repository: `RVC-Project/Retrieval-based-Voice-Conversion-WebUI`
- Revision: `81eed5e8f68b6bed1789f682fe78cdd324495afc`
- Revision date: 2026-08-04
- Code license: MIT
- Runtime: Python 3.12
- RTX 50-series Torch pair: `torch==2.7.1+cu128`, `torchaudio==2.7.1+cu128`

The upstream model/voice `.pth` and `.index` files are **not** covered merely because the RVC source code is MIT. Record the license/provenance of every target voice model used for evaluation.

## Why pin it

RVC's current main branch has materially different runtime assumptions from older snapshots. The feasibility result must identify exactly what was measured instead of silently tracking a moving upstream branch.

The harness therefore checks out one exact source commit under:

```text
engines/rvc/upstream/
```

and creates its own environment under:

```text
engines/rvc/.venv/
```

Nothing is installed into the main This Voice Thing environment.

## Commands

From the This Voice Thing repository root:

```bat
python scripts\rvc_feasibility.py status
python scripts\rvc_feasibility.py install
python scripts\rvc_feasibility.py assets
```

The installer requires `git` and `uv`. It intentionally removes the package-index directives from RVC's upstream requirements file and installs from official PyPI/PyTorch indexes.

The `assets` command downloads only the shared HuBERT and RMVPE inference assets used by RVC. A target voice model is still required.

Run one offline conversion:

```bat
python scripts\rvc_feasibility.py convert ^
  --model "D:\voices\target.pth" ^
  --input "D:\samples\source.wav" ^
  --output "D:\samples\converted.wav" ^
  --f0-method rmvpe ^
  --index-rate 0
```

If a legally usable added index is available:

```bat
python scripts\rvc_feasibility.py convert ^
  --model "D:\voices\target.pth" ^
  --index "D:\voices\added_target.index" ^
  --input "D:\samples\source.wav" ^
  --output "D:\samples\converted-indexed.wav" ^
  --index-rate 0.75
```

## Current upstream realtime design

The pinned upstream realtime implementation is useful architectural evidence but is not copied into This Voice Thing's routing layer.

At this revision it uses:

- default 250 ms audio blocks;
- crossfade + SOLA alignment between converted blocks;
- HuBERT features;
- RMVPE, FCPE, or PM pitch extraction;
- optional input/output noise reduction;
- pitch and formant controls;
- optional CUDA Graph acceleration;
- its own `sounddevice` input/output stream.

This Voice Thing should reuse the inference/window/SOLA ideas, **not** RVC's device-routing ownership. #30 owns microphone capture and the existing Live Voice stack owns AudioRouter/output.

## Phase 1 evidence record

Before proceeding to realtime worker integration, record:

```text
Date:
This Voice Thing commit:
RVC revision: 81eed5e8f68b6bed1789f682fe78cdd324495afc
Python:
Torch:
CUDA:
GPU:
RVC target model:
Target model license/provenance:
Index:
Index license/provenance:
F0 method:

Install reproducible: PASS / FAIL
Shared assets present: PASS / FAIL
Offline conversion: PASS / FAIL
Input duration:
Output duration:
Conversion wall time:
Subjective intelligibility:
Subjective target similarity:
Notes:
```

## Promotion gate

Do not wire RVC into the product UI merely because offline conversion succeeds.

The next phase requires:

1. target-machine offline smoke evidence;
2. #30 microphone source boundary;
3. a bounded realtime worker that emits normalized PCM without owning devices;
4. microphone-to-monitor and microphone-to-virtual-route latency measurements;
5. explicit code + target-model license evidence.

This is a feasibility harness, not a seventh model tile pretending research is a product feature.
