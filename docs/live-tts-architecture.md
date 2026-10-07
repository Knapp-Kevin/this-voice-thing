# Live TTS Architecture

Status: experimental implementation in progress.

This document defines how **This Voice Thing** handles low-latency speech delivery over its local API. It separates model capability from transport behavior so that native streaming, segmented fallback, and completed-file rendering do not get collapsed into one misleading "streaming" switch.

## Goals

- Start returning playable speech before a full response has finished rendering.
- Keep the existing Windows-first desktop architecture and isolated engine environments.
- Preserve normal completed-render quality and behavior.
- Make capability, provenance, and processing differences visible to API clients.
- Measure actual latency on target hardware instead of inheriting benchmark claims from unrelated GPUs.
- Keep one GPU generation active at a time until concurrency has evidence that it improves rather than harms throughput.

## Live delivery modes

### Native

The model yields waveform chunks while one utterance is still being generated.

Current implementation target:

- **VoxCPM2**
- model-native sample rate: 48 kHz
- output: mono signed 16-bit little-endian PCM
- API capability value: `native`

Native mode has the lowest theoretical time to first audio because synthesis does not need to complete a sentence or section before bytes can be returned.

### Segmented

The model completes a short speech section, returns it immediately, then generates the next section while the previous one can be playing.

Current implementation target:

- **Kokoro**
- model-native sample rate: 24 kHz
- output: mono signed 16-bit little-endian PCM
- API capability value: `segmented`

Segmented mode is intentionally not described as native streaming. It can still sustain continuous playback when section generation remains comfortably faster than section playback.

### Buffered

The complete render finishes before audio is returned.

This remains the default for normal file-oriented synthesis and for engines without a validated live path.

## HTTP contract

The existing OpenAI-compatible endpoint is extended rather than replaced:

```http
POST /v1/audio/speech
Content-Type: application/json
```

Live request:

```json
{
  "model": "VoxCPM2 voice cloning",
  "input": "Hello from live speech.",
  "voice": "My saved voice",
  "stream": true,
  "stream_format": "audio",
  "response_format": "pcm"
}
```

Live responses use HTTP/1.1 chunked transfer and include:

- `Content-Type: audio/pcm`
- `X-Audio-Sample-Rate`
- `X-Audio-Sample-Format: s16le`
- `X-Audio-Channels: 1`
- `X-Streaming-Mode: native|segmented`
- `X-Audio-Watermark`

The first implementation accepts only raw PCM for live delivery. WAV, FLAC, and MP3 remain completed-render formats.

## Model capability metadata

Live support is declared by model configuration rather than inferred from a repository name.

Example:

```json
{
  "live_audio": {
    "mode": "native",
    "response_format": "pcm",
    "sample_rate": 48000
  }
}
```

Unknown or user-added models default to no advertised live support until the configuration explicitly declares it or a future runtime capability probe confirms it.

This is deliberate. A compatible file layout does not prove identical inference behavior.

## Engine worker protocol

Optional engines run in isolated Python environments. Ordinary worker requests retain the existing one-request/one-reply JSON-lines protocol.

Native streaming extends that protocol to an event sequence:

```text
request
  -> start
  -> audio
  -> audio
  -> ...
  -> done
```

Conceptual events:

```json
{"ok":true,"event":"start","sample_rate":48000,"format":"s16le","channels":1}
{"ok":true,"event":"audio","data":"<base64 PCM>","samples":...}
{"ok":true,"event":"done","samples":...,"chunks":...,"seconds":...,"generation_seconds":...,"ttfa_seconds":...}
```

Audio is Base64 inside the worker protocol because that keeps stdout line-oriented and deterministic on Windows. The HTTP layer decodes the event payload back to binary PCM before sending it to the client.

The worker lock remains held until the terminal event. A second request must never consume unread events from the previous stream.

## Cancellation

A client disconnect must not corrupt the worker protocol.

The base transport therefore drains an abandoned worker stream to its terminal event before unlocking. A dependent cancellation slice improves this by giving each native request a same-machine cancellation sentinel. On early consumer close, the main process creates the sentinel. The VoxCPM worker checks it between native chunks, closes the upstream generator, emits a terminal `done` event, and releases the slot.

Cancellation is cooperative at chunk boundaries. It is not advertised as instantaneous.

Segmented engines naturally stop between completed sections when their Python generator is closed.

## Rendering and provenance policy

Live output is **not** identical to completed rendering.

Completed rendering can perform operations over the whole waveform:

- global volume levelling
- final silence trimming
- formant-preserving speed and pitch processing
- final subtitle alignment
- encoded export to WAV, FLAC, or MP3
- final section joining

Those operations cannot simply be applied independently to arbitrary live chunks without changing their meaning.

### Native VoxCPM2 live path

The first native path is intentionally raw:

- no whole-waveform finishing
- no subtitle alignment
- speed fixed at 1.0
- raw PCM only
- the normal Perth watermark is not currently applied

The API reports this provenance difference with `X-Audio-Watermark`.

### Kokoro segmented path

Kokoro completes each short section before delivery, so its existing per-section watermark can remain applied.

The segmented path also preserves the app's existing clause, sentence, paragraph, and heading pause rules between sections.

## Concurrency

The first live implementation preserves the existing single-generation API/GPU lock.

This is intentional. Multiple simultaneous inference jobs on one desktop GPU can increase memory pressure and worsen latency. Concurrency should be introduced only after measurements show a real benefit.

Multi-tenant server deployment is a different problem and may eventually use a dedicated serving runtime rather than the desktop worker architecture.

## Metrics

Every live implementation is evaluated with the same metrics.

### Time to first audio (TTFA)

Wall-clock time from beginning the HTTP request to receiving the first playable PCM bytes.

Initial engineering targets:

- under 500 ms: useful
- under 300 ms: excellent

These are project targets, not guarantees.

### Real-time factor (RTF)

```text
RTF = generation wall time / generated audio duration
```

Interpretation:

- `RTF < 1.0`: generation stays ahead of playback
- `RTF < 0.5`: comfortable buffering headroom
- `RTF > 1.0`: continuous playback will eventually underrun

### Chunk cadence

Measure the time between delivered audio chunks or completed speech sections. Average RTF alone can hide long individual stalls.

### Cancellation release latency

After the client disconnects, measure how long the API remains busy before the generation slot is released.

This catches cancellation implementations that stop network delivery but continue wasting GPU time.

## Benchmark tooling

`scripts/benchmark_live_api.py` is the canonical client-side benchmark.

It measures:

- TTFA
- total wall time
- generated audio duration
- RTF
- sample rate

The dependent cancellation slice also measures slot-release latency after disconnect.

Results should be recorded from the actual target Windows machine and GPU. Published upstream H100, RTX 4090, or other hardware results are useful architectural evidence, not local performance claims.

## Current engine matrix

| Engine | Current live mode | Notes |
| --- | --- | --- |
| VoxCPM2 | Native | First transport implementation; raw PCM; 48 kHz |
| Kokoro | Segmented | Dependent slice; short sections; 24 kHz; per-section watermark |
| Chatterbox multilingual | Buffered | Realtime-oriented variants should be evaluated separately |
| Qwen3-TTS | Buffered | Current direct path is optimized heavily by batching, which conflicts with TTFA |
| OmniVoice | Buffered | High throughput; native streaming should wait for a validated integration path |
| VibeVoice 1.5B | Buffered | Long-form conversation model, not the realtime VibeVoice architecture |

## Validation gate

No live implementation is production-ready merely because the code path exists.

Before merging to `main`:

1. Run the unit suite from a real checkout.
2. Run the live path on Windows.
3. Verify first audio arrives before synthesis completes.
4. Record TTFA and RTF on the target RTX 5070 Ti.
5. Listen for chunk or section boundary artifacts.
6. Compare live output with the same engine's buffered output.
7. Verify a failed or abandoned stream cannot poison the next worker request.
8. Verify the API/GPU lock is always released.
9. Verify provenance headers match actual processing.
10. Confirm normal buffered speech and transcription endpoints remain unchanged.

## Merge order

The work is intentionally split:

1. **Native transport and VoxCPM2 proof**: foundational worker and HTTP streaming contract.
2. **Native cancellation**: cooperative cancellation without stale worker events.
3. **Kokoro segmented fallback**: proves the transport supports a second, non-native live mode.
4. **Incremental text input**: future WebSocket or equivalent path for feeding clauses from a still-generating LLM.
5. **Additional engines**: only after capability, licensing, quality, and latency evidence supports them.

## Non-goals for the first release

- multi-user GPU scheduling
- arbitrary live MP3/FLAC encoding
- pretending whole-waveform finishing is chunk-safe
- exposing the local API beyond loopback by default
- claiming every fast TTS model is a native streaming model
- adding WebSockets before streamed audio output itself is measured and stable

The simplest useful system is preferred: one local request, one model, truthful capability metadata, immediate PCM when the engine can provide it, and measurements proving whether it is actually live enough to matter.
