# Live Voice Microphone Transformation Roadmap

Status: governed roadmap / implementation handoff  
Date: 2026-10-06  
Method: QOR Roadmap topology + QOR Plan decomposition

## Objective

Extend Live Voice so a user can speak into a microphone and send transformed speech through the same local-output, monitoring, virtual-microphone, Discord, Zoom, OBS, and generic-app routing system already used by typed TTS and soundboard playback.

The architecture must support two microphone transformation lanes:

1. **Mic Effects** — low-latency conventional DSP that modifies the user's existing voice.
2. **AI Voice Conversion** — neural speech-to-speech voice conversion that changes speaker identity without ASR → text → TTS.

## Success conditions

- microphone audio can enter Live Voice as a first-class source;
- microphone processing never requires transcription for normal voice-changing;
- Mic Effects remains useful on modest hardware and without a neural model loaded;
- AI Voice Conversion runs as an isolated optional engine and cannot contaminate the main application environment;
- transformed PCM uses the same routing, monitoring, Stop, diagnostics, fail-closed external-route, and virtual-microphone semantics as the rest of Live Voice;
- voices expose truthful capabilities instead of implying every saved voice works with every transformation engine;
- user-facing neural conversion is gated by measured latency, stability, licensing, and target-machine evidence.

## Exclusions

This roadmap does not make This Voice Thing:

- a DAW;
- a multitrack editor;
- a VST/plugin host;
- an ASR → LLM → TTS voice changer;
- a custom Windows audio-driver project;
- an embedded Discord or Zoom client;
- a persistent microphone recorder by default.

## North Star

> Pick a voice or effect, pick where you want to speak, and talk.

The application should absorb the model, buffering, device, and routing complexity beneath that sentence.

## Architecture

```text
                                      ┌─ Text
                                      │    ↓
                                      │  TTS engine
                                      │
Live Voice source ────────────────────┼─ Microphone
                                      │    ↓
                                      │  DSP effects
                                      │
                                      └─ Microphone
                                           ↓
                                      Neural voice conversion
                                           │
                                           ▼
                                        AudioFrame
                                           │
                                           ▼
                                       AudioRouter
                                    /              \
                              primary route       monitor
                                    │
                         local output / virtual mic
                                    │
                         Discord / Zoom / OBS / app
```

The source owns capture/transformation. The router owns delivery.

Do not put microphone capture, RVC inference, or DSP processing inside `LiveSpeechSession`. Text/TTS and microphone conversion are sibling sources that emit the same PCM contract.

## Resolved facts

### F1 — Live Voice already has a normalized PCM boundary

Evidence:

- `this_voice_thing/core/live_voice.py::AudioFrame`
- `LiveSpeechSession`, `CachedSpeechSession`, and `AudioFileSpeechSession` already converge on PCM frames.

Consequence:

Microphone sources should emit the same normalized frame contract rather than invent another routing format.

### F2 — Output routing is already independent of TTS generation

Evidence:

- `this_voice_thing/ui/live_audio.py`
- current route profiles, primary/monitor sinks, virtual-cable routing, Stop semantics, and diagnostics in canonical Live Voice PR #24.

Consequence:

Mic Effects and AI Voice Conversion can reuse output routing instead of reimplementing Discord/Zoom/OBS integration.

### F3 — Speech-to-speech voice conversion does not require ASR → TTS

Evidence:

- `docs/live-voice-advanced-integrations.md`
- issue #29 research scope.

Consequence:

The voice-changer path should preserve direct microphone timing/prosody and must not make transcription a normal dependency.

### F4 — RVC is the preferred first neural prototype

Evidence:

- completed advanced-integration research in `docs/live-voice-advanced-integrations.md`;
- existing issue #29;
- MIT code license and mature realtime VC ecosystem.

Consequence:

RVC remains the first candidate for proving the isolated neural-worker architecture.

### F5 — Seed-VC Realtime is attractive but not a current implementation dependency

Evidence:

- zero-shot reference-voice conversion is appealing for the existing saved-voice UX;
- current Seed-VC / Seed-VC Realtime code is GPL-3.0;
- the original upstream is archived and the active realtime implementation is a continuation.

Consequence:

Keep Seed-VC as a later comparison/evidence node. Do not couple v1 architecture to it.

### F6 — Conventional DSP provides a low-resource voice-changing lane

Pitch/formant transformation, EQ, compression, gain, noise management, and optional character effects can operate without a neural conversion model.

Consequence:

Mic Effects should be useful independently and should share microphone capture with the neural path.

## Authority decisions

### D1 — Product exposes three Live Voice source modes

Decision:

- **Live Speak** — text → TTS;
- **Mic Effects** — microphone → DSP;
- **AI Voice Conversion** — microphone → neural VC.

Authority: Product owner  
Status: resolved 2026-10-06.

### D2 — Microphone conversion is not implemented as ASR → TTS

Authority: Product owner  
Status: resolved 2026-10-06.

ASR may remain an unrelated transcription feature. It is not part of the realtime voice-changer signal path.

### D3 — Routing remains shared

All three source modes feed the existing AudioRouter/output abstraction.

Authority: architecture  
Status: resolved.

### D4 — Neural engines remain isolated

RVC and future VC engines use an isolated optional environment/process/worker boundary. They do not add incompatible model dependencies to the main Python environment.

Authority: architecture  
Status: resolved.

### D5 — External transmission remains explicit

Selecting a microphone transformation source does not silently arm an external route. Existing route arming and fail-closed semantics remain authoritative.

Authority: safety/product  
Status: resolved.

## Prerequisites

### P1 — Current Live Voice release gate

User-facing microphone conversion integration depends on #13 passing for the current Live Voice routing stack.

An isolated RVC benchmark may proceed before #13 as long as it does not modify the product route/runtime surface.

### P2 — Shared microphone source

A reusable microphone capture/session abstraction must exist before product-level DSP or neural integration.

It owns:

- input-device selection;
- mono PCM normalization;
- bounded ring/buffer behavior;
- VAD/silence semantics where needed;
- cancellation/Stop;
- device-disconnect reporting;
- capture diagnostics.

It does not own routing.

### P3 — Neural model/weight licensing

RVC code licensing does not automatically grant rights to arbitrary trained voice models or weights.

Any model shipped or distributed by This Voice Thing needs its own license/provenance review.

## Unresolved spaces

### U1 — Zero-shot VC engine

Question:

Does Seed-VC Realtime or another permissively distributable zero-shot engine provide enough quality/latency advantage to justify adding a second VC engine?

Resolution trigger:

Only after the RVC benchmark establishes a baseline.

### U2 — RVC target-voice creation UX

Question:

Should This Voice Thing eventually train/manage RVC target voices itself, import already-trained models, or support both?

Resolution trigger:

RVC prototype proves technical viability and exposes actual model preparation requirements.

### U3 — Branded virtual microphone

Still deferred under the advanced-integration ADR.

It is not required for microphone transformation because existing virtual-audio devices already provide cross-application routing.

## Planning scopes

## Scope S1 — Microphone source + Mic Effects foundation

Status: **IMPLEMENTED / runtime evidence delegated to #13**

Purpose:

Create the low-resource microphone path and prove that realtime capture can reuse the existing Live Voice output architecture without neural inference.

### Deliverables

- `MicrophoneAudioSession` or equivalent source abstraction;
- microphone input-device selection;
- bounded capture/ring buffer;
- raw pass-through mode;
- small composable DSP chain;
- initial effects:
  - input gain;
  - pitch shift;
  - formant shift where the selected implementation supports it cleanly;
  - EQ/basic tone shaping;
  - optional compressor/limiter;
- Mic Effects controls in Live Voice;
- reuse of current route, monitor, Stop, diagnostics, and fail-closed behavior;
- no recording persistence unless explicitly requested by a future feature.

### Initial targets

- DSP path should not require GPU inference;
- added application processing should remain small relative to ordinary device buffering;
- initial product target: <= 100 ms microphone-to-monitor where the host audio stack permits it;
- no unbounded buffering;
- no audio callback waits on slow processing.

### Definition of Done

- **D1:** user can choose a microphone, choose Mic Effects, choose a route, and speak through it.
- **D2:** microphone capture emits the shared PCM/frame contract and routing remains independent.
- **D3:** README/architecture documentation describes source vs route ownership and privacy behavior.
- **D4:** unit tests cover bounded buffering, Stop, device-loss signaling, and DSP transform output; target-Windows runtime measurements are recorded before stable release.

## Scope S2 — Isolated RVC prototype

Status: **PORTABLE PROTOTYPE COMPLETE / TARGET EVIDENCE BLOCKED**

Tracking: issue #29.

Purpose:

Prove real microphone → neural VC → PCM performance without coupling the experimental engine into the main runtime.

### Deliverables

- isolated RVC environment/worker;
- offline conversion smoke test;
- realtime block/ring-buffer prototype;
- microphone-to-monitor benchmark;
- microphone-to-virtual-route benchmark;
- GPU/CPU/VRAM measurements;
- model/weight license record;
- GO / NO-GO recommendation for product integration.

### Initial targets

- desirable: < 500 ms microphone-to-output;
- stretch: < 300 ms;
- inference must not routinely exceed the audio block deadline without explicit bounded buffering behavior.

### Definition of Done

- **D1:** RVC feasibility is established with measured evidence.
- **D2:** worker boundary emits normalized PCM compatible with the shared audio source/router contract.
- **D3:** code and model/weight licensing are documented separately.
- **D4:** target-machine latency, utilization, dropout, Stop, and route measurements are recorded.

## Scope S3 — Capability-aware Voice Changer product integration

Status: **BLOCKED**

Requires:

- S1 implementation evidence;
- S2 GO decision;
- #13 current Live Voice routing validation.

Purpose:

Make Mic Effects and AI Voice Conversion feel like one coherent Live Voice product instead of two unrelated engineering demos.

### Product behavior

Live Voice source selector:

- Live Speak
- Mic Effects
- AI Voice Conversion

Voice library capability examples:

```text
Voice: Alice
TTS                 ✓
RVC target           ✓
Zero-shot VC         not available

Voice: Bob
TTS                 ✓
RVC target           not configured
Zero-shot VC         reference clip available
```

The UI must never imply that a TTS reference clip is automatically a trained RVC target model.

### Deliverables

- source-mode selector;
- capability-aware saved-voice metadata;
- engine/model readiness and resource status;
- Mic Effects controls;
- AI Voice Conversion target selection;
- low-latency/balanced/low-resource conversion presets if benchmark evidence supports those distinctions;
- one shared route/monitor/Stop surface regardless of source mode;
- clear microphone-capture and external-route state.

## Scope S4 — Microphone transformation validation

Status: **BLOCKED**

Requires S3.

Extend the Live Voice QA contract with:

- raw microphone pass-through latency;
- DSP path latency;
- RVC mic-to-monitor latency;
- RVC mic-to-virtual-route latency;
- sustained block deadline performance;
- GPU/CPU/VRAM;
- dropout/underrun count;
- device unplug/replug;
- input-device switching;
- Stop latency;
- feedback resilience;
- Discord/Zoom/OBS listening tests;
- long-session soak;
- route remains disarmed after restart;
- no persisted microphone recording without explicit user action.

## Actionable frontier

Current frontier state:

1. **S1 microphone source + Mic Effects foundation — implemented** in canonical Live Voice PR #24; remaining physical microphone/latency/soak evidence is owned by #13.
2. **S2 isolated RVC benchmark prototype — portable implementation complete** in PR #33; the remaining legal action is the target Windows/NVIDIA install + target-model benchmark and GO / NO-GO / RESEARCH-MORE decision under #29.

S3 remains blocked until:

- #29 produces a GO decision from real benchmark evidence; and
- #13 validates the shared route/monitor/Stop foundation on the target Windows system.

S4 is not ready until S3 exists.

No implementation ticket should currently be created for Seed-VC or a custom Windows virtual microphone.

## Current evidence state

As of 2026-10-07:

- S1 portable implementation and CI are complete; microphone runtime acceptance remains centralized in #13.
- S2 portable RVC worker/facade/benchmark implementation is complete in PR #33 and green on Windows/Linux CI.
- S2 now consumes the same shared microphone `AudioFrame` contract established by S1 through a UI-free `RVCFrameAdapter`.
- microphone capture discontinuities are explicit in `AudioFrame`; the RVC worker resets temporal input/resample/RMS/SOLA state and pitch caches without reloading model weights.
- the RVC benchmark feeds 20 ms microphone-like source frames through the adapter rather than bypassing it with perfectly aligned neural blocks.
- target-machine RVC install/model benchmark evidence is still missing because the authorized Windows/NVIDIA host is unavailable.
- no product-facing AI Voice Conversion UI should be added until #29 produces a GO decision.

## Expected product sequence

```text
Current Live Voice validated (#13)
          │
          ├───────────────┐
          ▼               ▼
   Mic source + DSP     RVC prototype
          │               │
          └───────┬───────┘
                  ▼
       capability-aware integration
                  │
                  ▼
          transformation QA
                  │
                  ▼
           stable Voice Changer
```

This keeps the architecture simple: transformation sources produce audio; AudioRouter delivers audio. Neural inference, DSP, and TTS remain separate concerns rather than being braided into one increasingly haunted class.
