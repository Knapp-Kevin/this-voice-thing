# Live Voice Adversarial Design Review

Status: pre-implementation challenge  
Outcome: proceed, with required mitigations below

## Executive assessment

The Live Voice concept is technically feasible and fits the current architecture, but several apparently simple ideas are traps:

1. "Output to Discord" is not the same thing as a Discord API integration.
2. A virtual microphone is normally a paired render/capture driver, so This Voice Thing writes to an output endpoint even though the user thinks of it as a microphone.
3. Device fallbacks that are convenient for music players are dangerous for live-call routing.
4. Soundboard latency expectations are much stricter than ordinary TTS latency.
5. Existing conferencing noise suppression can damage synthetic speech.
6. A custom virtual microphone would improve onboarding but introduces kernel-driver signing and distribution complexity.
7. Cancellation has two meanings: audible cancellation and inference cancellation.
8. Multiple sinks create backpressure and feedback-loop risks.
9. Stale soundboard caches can silently speak with the wrong voice/settings if invalidation is weak.
10. Global hotkeys create accidental-transmission and key-conflict risks.

The proposal survives adversarial review if the following decisions are treated as requirements rather than suggestions.

## Challenge 1: "Can we just send audio directly to Discord?"

### Attack

Assume Discord provides an API that lets an arbitrary desktop utility inject PCM into the user's existing Discord desktop call.

### Finding

Do not assume this.

Discord's Social SDK exposes audio devices and communication features, but it is positioned for embedding Discord-powered communication inside games/applications. The evidence supports device selection and integrated voice, not a general-purpose "inject this PCM into my already-running desktop Discord client" contract.

Sources:

- https://discord.com/developers/social-sdk
- https://discord.com/developers/docs/social-sdk/classdiscordpp_1_1AudioDevice.html

### Decision

Use a virtual microphone for normal Discord-client support.

Keep a separate future Social SDK feasibility spike. Do not advertise it as a direct integration until the exact use case is documented and permitted.

## Challenge 2: "Can we just send audio directly to Zoom?"

### Attack

Assume the Zoom desktop client accepts an external PCM API.

### Finding

Zoom's Meeting SDK can publish custom PCM in SDK-controlled meeting experiences, including Production Studio mode on Windows/macOS. That is not the same as pushing arbitrary PCM into a user's separately running Zoom Workplace client.

Production Studio also has role/permission constraints.

Source:

- https://developers.zoom.us/docs/meeting-sdk/windows/default-ui/advanced-features/production-studio-mode/

### Decision

Use a virtual microphone for normal Zoom-client support.

Evaluate direct Meeting SDK publishing only as a specialized embedded-Zoom product track.

## Challenge 3: "Just use the default audio device if the cable is missing"

### Attack

A saved Discord route targets a virtual cable. Windows renumbers/removes the device after update/reboot. The app falls back to Default Speakers.

### Consequence

Private or disruptive speech intended for a call is unexpectedly broadcast locally.

### Decision

External routes fail closed.

Persist device ID plus display metadata. If the exact target cannot be resolved, mark the route unavailable and require repair.

No default-device fallback for an armed external route.

## Challenge 4: audio feedback loop

### Attack

Live Voice writes to speakers for monitoring. A physical microphone captures the speakers and sends the same speech into Discord in addition to the virtual route.

### Consequence

Echo, doubled speech, feedback, or noise-cancellation artifacts.

OBS explicitly warns that duplicate audio-device capture can create echo.

Source:

- https://obsproject.com/kb/audio-sources

### Decision

- headphones are the recommended monitoring path
- speaker monitoring defaults off when an external route is armed
- show a feedback warning when monitor and likely capture topology look risky
- independent monitor mute/volume
- app profile setup calls this out explicitly

## Challenge 5: conferencing software destroys the audio

### Attack

Discord Krisp or Zoom noise suppression treats TTS as noise, gates quiet phonemes, alters dynamics, or clips tails.

### Evidence

Discord documents Krisp as ML-based noise filtering. Zoom documents background suppression and echo cancellation, and explicitly recommends Original Sound/high-fidelity modes for cases needing less processing.

Sources:

- https://support.discord.com/hc/en-us/articles/360040843952-Krisp-FAQ
- https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0059985

### Decision

App profiles include processing guidance and a route test.

Do not automatically mutate another app's settings without a supported API and explicit permission.

Prefer 48 kHz output to virtual routes where supported.

## Challenge 6: sample-rate mismatch

### Attack

Kokoro emits 24 kHz. A virtual cable or conferencing workflow expects 48 kHz. Qt rejects the format or the OS resamples unpredictably.

### Decision

AudioRouter owns sink negotiation and stateful resampling.

The engine source remains native-rate. Each sink receives its negotiated rate.

Metrics record source rate and sink rate.

Resampling must preserve stream continuity across chunk boundaries.

## Challenge 7: "First model chunk" is not necessarily playable latency

### Attack

Start QAudioSink on a tiny initial chunk.

### Consequence

Immediate underrun, click/pop, then a pause before subsequent audio.

### Decision

Separate:

- model TTFA
- first playable buffer latency
- first audible output latency

Begin playback only after a configurable minimum buffer threshold or when the source declares enough audio.

Measure underruns.

## Challenge 8: soundboard feels slow

### Attack

Every pad triggers full model load/inference.

### Consequence

A "soundboard" whose button takes one second or ten seconds is merely a collection of future appointments.

### Decision

Static pads cache rendered audio.

Cached playback must not require the TTS model to be loaded.

Target trigger-to-audio <= 100 ms.

Dynamic pads explicitly show that they are dynamic.

## Challenge 9: stale cache speaks the wrong thing

### Attack

User changes voice design, pronunciation dictionary, model, or style. Old pad cache still plays.

### Decision

Content-addressed cache key includes all synthesis-affecting inputs and schema version.

If a referenced voice changes, its fingerprint/revision changes.

Never treat pad ID as sufficient cache identity.

## Challenge 10: soundboard cache leaks private content

### Attack

Meeting phrases or names persist indefinitely in cached WAV files.

### Decision

- document that caches are local
- allow Clear Soundboard Cache
- configurable history/cache retention later
- do not sync or upload by default
- deletion of a pad should garbage-collect unreferenced owned cache artifacts

## Challenge 11: queue grows without bound

### Attack

Auto Speak, hotkeys, or repeated pad triggers enqueue faster than speech plays.

### Decision

Bound queue length and total estimated queued duration.

Configurable repeated-trigger behavior:

- enqueue
- replace duplicate
- ignore while already queued
- interrupt

Show queue pressure visibly.

## Challenge 12: Stop does not actually stop

### Attack

UI clears queue but audio already buffered in QAudioSink keeps speaking.

### Decision

Stop Current must:

1. cancel generation
2. reset/stop QAudioSink immediately
3. discard ring-buffer PCM
4. remove current item
5. start next only if user requested Stop Current rather than Stop All

Test audible-stop latency separately from worker cancellation latency.

## Challenge 13: native worker cancellation is delayed

### Attack

Client/UI cancels while the engine is inside a long model step and cannot observe cancellation until the next chunk boundary.

### Decision

Document cooperative cancellation.

Audible output still stops immediately because sink buffer is reset.

Inference release latency is separately measured.

## Challenge 14: multiple sinks deadlock each other

### Attack

Headphone monitor device stalls while the virtual cable remains healthy.

### Decision

Do not synchronously write one frame to every sink on the generation thread.

Use independent bounded sink buffers.

A failed monitor must not break the primary route unless the user explicitly selects all-or-nothing routing.

## Challenge 15: device unplug/replug

### Attack

USB headphones disappear during a call.

### Decision

Primary external route:
- fail closed if it disappears

Monitor:
- stop monitor and surface warning
- keep primary route alive

Listen to Qt device-change signals and re-evaluate routes.

Never silently migrate an armed primary external route.

## Challenge 16: global hotkey accident

### Attack

A pad hotkey conflicts with a game/application shortcut and speaks unexpectedly into a live call.

### Decision

Global hotkeys are opt-in.

Provide:

- conflict detection where practical
- a visible global-hotkeys-enabled state
- per-pad enable/disable
- emergency Stop All hotkey
- optional require-modifier policy
- no default global pad bindings

## Challenge 17: Auto Speak transmits unfinished text

### Attack

User pauses mid-sentence and the debounce sends an embarrassing fragment.

### Decision

Auto Speak is not default.

Commit rules require punctuation or explicit configured boundaries plus idle threshold.

Show pending versus committed text.

Undo cannot retract already transmitted speech, so the product must make commitment obvious.

## Challenge 18: external route stays armed after context switch

### Attack

User finishes a Discord session, later opens the app and triggers a soundboard expecting local playback.

### Decision

External routes disarm on fresh application launch by default.

Potential "remember armed for session" is session-scoped, not perpetual.

Route/Armed state is permanently visible.

## Challenge 19: third-party virtual cable licensing/distribution

### Attack

Bundle VB-CABLE casually because it is easy to download.

### Finding

VB-Audio describes VB-CABLE as donationware and publishes explicit distribution/professional licensing expectations.

Sources:

- https://vb-audio.com/Cable/index.htm
- https://vb-audio.com/Services/licensing.htm

### Decision

First release detects/supports third-party virtual devices but does not silently bundle them.

Any bundled installer work requires explicit licensing review and attribution/compliance.

## Challenge 20: build our own virtual mic immediately

### Attack

A custom "This Voice Thing Mic" seems like the obvious UX win.

### Consequence

Scope expands into WDM/WaveRT driver work, code signing, admin installer, update/uninstall, support, and system stability.

Microsoft's SysVAD sample proves the technical route exists, not that it is cheap.

Source:

- https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/sysvad-virtual-audio-device-driver-sample/

### Decision

Make custom virtual mic a separate feasibility project after generic routing is successful.

## Challenge 21: Qt thread misuse

### Attack

Generation thread directly manipulates `QAudioSink`/UI objects while model chunks arrive.

### Decision

Keep UI/Qt object ownership disciplined.

Use queued signals or a dedicated audio-owned object/thread as needed.

Generation does not directly touch UI widgets.

Stress test rapid start/stop/device switching.

## Challenge 22: audio clipping and gain mismatch

### Attack

Generated PCM peaks near full scale, then conferencing automatic gain control boosts/clips it.

### Decision

Introduce a stream-safe output gain and optional limiter at the router/sink stage.

Do not reuse whole-waveform global loudness normalization chunk-by-chunk.

Expose a route test meter.

## Challenge 23: app-specific profiles become brittle automation

### Attack

We attempt to click through Discord/Zoom settings or depend on UI element names.

### Decision

Initial profiles are deterministic instructions + device testing, not fragile UI automation.

If an app provides a stable supported API later, add an adapter behind the profile.

## Challenge 24: voice identity misuse

### Attack

A stored cloned voice is used without permission or is mislabeled.

### Decision

The app cannot prove consent, but it can improve provenance:

- preserve voice origin fields
- clearly identify active voice
- preserve watermarks where supported
- surface when the live path is unwatermarked
- do not present synthetic voice output as identity authentication

Potential future feature: optional consent/source notes on cloned voices.

## Challenge 25: accessibility feature becomes unusable under failure

### Attack

An essential phrase depends on GPU model availability during an urgent interaction.

### Decision

Allow important static phrases to be cached/pre-rendered.

A designated "Essential" board should be able to function without the synthesis model loaded as long as caches exist.

## Challenge 26: soundboard becomes an unrelated audio workstation

### Attack

Feature requests add mixing, DAW effects, multitrack timelines, plugins, buses, etc.

### Decision

Scope rule:

Live Voice owns **speech generation, simple clips, routing, monitoring, queueing, and stream-safe output controls**.

It does not become a DAW.

Complex production routing belongs in OBS, VoiceMeeter, a DAW, or another specialized tool.

## Go / no-go gates

### Gate A: streaming foundation

Proceed only after:

- native stream produces audible output before completion
- segmented mode is measured
- cancellation cannot poison worker protocol
- normal buffered generation remains intact

### Gate B: local Live Speak

Proceed to external routing after:

- QAudioSink playback survives 30-minute soak
- zero unexplained underruns
- Stop All reliably silences output
- device switching does not crash

### Gate C: virtual microphone

Proceed to app profiles after:

- generic virtual cable works on Windows
- missing-device behavior fails closed
- dual monitoring works without blocking
- 48 kHz path is validated

### Gate D: Soundboard

Ship after:

- cached pad target latency met
- cache invalidation tests pass
- queue bounds verified
- per-pad voice switching is deterministic
- emergency stop works during repeated hotkey/pad triggering

### Gate E: Discord/Zoom profile

Ship after:

- clean setup from fresh configuration
- route test clearly identifies wrong-device setup
- documented processing recommendations verified manually
- no claim of unsupported direct integration

## Verdict

Proceed.

The strongest architecture is not "integrate separately with Discord and Zoom." It is:

```text
live speech + soundboard
          ↓
     AudioRouter
          ↓
standard audio endpoint
          ↓
any application that accepts that endpoint
```

Direct SDK integrations remain optional optimizations. This keeps the product broadly useful, lowers coupling, and lets the existing streaming work become a reusable desktop capability rather than an API-only feature.
