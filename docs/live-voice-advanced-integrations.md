# Live Voice Advanced Integration Research

Status: decision record  
Date: 2026-10-06  
Tracking: issue #14

This document closes the first research pass for four advanced Live Voice integration tracks:

1. a branded **This Voice Thing Virtual Mic** for Windows
2. direct **Zoom SDK PCM publishing**
3. **Discord Social SDK** integration
4. true **microphone-to-microphone realtime voice conversion**

The primary Live Voice architecture remains unchanged: generic Windows audio routing and existing virtual-cable devices are the default cross-application integration path. None of the research tracks below is required for Discord, Zoom, OBS, games, browser calls, or other applications that can select a microphone/input device.

## Executive decisions

| Track | Decision | Why |
| --- | --- | --- |
| This Voice Thing Virtual Mic | **DEFER / conditional GO later** | Technically feasible, but it is a signed Windows driver product with installer, update, compatibility and support obligations. Build only after generic virtual-cable routing proves adoption and setup friction justifies it. |
| Zoom Meeting/Video SDK PCM | **NO-GO for normal Zoom-client routing; conditional GO for an embedded Zoom mode** | Zoom exposes real PCM send paths, but they live inside SDK-controlled meeting/video experiences. They do not provide a general way to inject arbitrary PCM into a separately running Zoom Workplace client. |
| Discord Social SDK | **NO-GO for the desktop utility integration** | Discord positions the SDK around games and in-game social/voice experiences, with communication-feature approval requirements. It does not solve "speak into the user's normal Discord desktop call" better than a virtual microphone. |
| Realtime voice conversion | **GO for a separate optional-engine prototype after Live Voice routing is validated** | Technically viable and product-relevant. Keep it separate from TTS. RVC is the preferred first feasibility candidate because its main project is MIT; Seed-VC Realtime is technically attractive but GPL-3.0. |

## 1. This Voice Thing Virtual Mic

### Question

Should the application eventually ship its own Windows virtual microphone so users do not need to install/configure a third-party virtual audio cable?

### Technical feasibility

**Yes.**

Microsoft's SysVAD sample demonstrates a software-backed WDM virtual audio device using the Windows audio driver stack and WaveRT. It is explicitly intended as a starting point for virtual audio device development.

Microsoft sample:

- https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/sysvad-virtual-audio-device-driver-sample/
- https://learn.microsoft.com/en-us/windows-hardware/drivers/samples/audio-driver-samples

The product shape we want is straightforward conceptually:

```text
This Voice Thing
      ↓
This Voice Thing Virtual Mic (render endpoint)
      ↓
driver ring/buffer
      ↓
This Voice Thing Mic (capture endpoint)
      ↓
Discord / Zoom / Teams / game / browser
```

The difficult part is not moving PCM. The difficult part is responsibly shipping and maintaining a kernel-mode Windows driver.

### Signing and distribution

Microsoft requires kernel-mode drivers on modern Windows to be signed through the Windows Hardware Developer Center. A Hardware Developer Program account requires an EV code-signing certificate.

Microsoft currently recommends HLK-tested/dashboard-signed drivers for production. Attestation signing is described as a testing-oriented path and cannot be published to Windows Update for normal retail audiences.

References:

- https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/
- https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/driver-signing-offerings
- https://learn.microsoft.com/en-gb/windows-hardware/drivers/dashboard/code-signing-reqs
- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-signing

Current practical obligations include:

- EV code-signing certificate and Hardware Dev Center account
- WDK/Visual Studio driver build environment
- INF/catalog/package management
- administrator-elevated install/uninstall
- Microsoft signing submission
- Secure Boot-compatible validation
- HLK testing for the recommended production path
- installer rollback/recovery
- driver update/version compatibility
- crash/debug/support procedures
- x64 and potentially ARM64 packaging
- Windows release-regression testing

### Product value

A branded virtual microphone would materially improve onboarding.

Instead of:

```text
Install third-party virtual cable
Find playback side
Find recording side
Remember their reversed Input/Output names
Configure both apps
```

the experience becomes:

```text
Send voice to: This Voice Thing Mic
Target app microphone: This Voice Thing Mic
```

That is a real product advantage, especially for accessibility users and nontechnical users.

### Risks

- Kernel-mode defects have much larger support consequences than Python/UI defects.
- Audio-driver updates can interact with Windows security, device enumeration and conferencing software.
- Signing/certification creates operational work outside normal application release flow.
- A driver increases installer trust friction, including elevation and security warnings.
- The support matrix becomes Windows-version and architecture sensitive.

### Recommendation

**DEFER, with a conditional GO after Live Voice adoption is measured.**

Do not make this a dependency of the first stable Live Voice release.

Revisit when all of the following are true:

- generic virtual-cable routing is validated and stable
- users demonstrably value Discord/Zoom routing
- virtual-cable installation/setup is a major support/onboarding failure point
- the project is willing to own a signed Windows driver lifecycle
- a dedicated installer/update strategy exists

### Estimated effort

These are planning estimates, not commitments:

- SysVAD proof-of-concept exposing a paired render/capture endpoint: **2–4 engineer-weeks**
- application-to-driver data path + installer prototype: **2–4 additional weeks**
- production signing, HLK work, recovery/update testing, Windows-version hardening: **4–10+ additional weeks**
- ongoing support: **meaningful permanent release burden**

A production-quality virtual mic is therefore closer to a small platform project than a normal feature issue.

## 2. Zoom direct PCM publishing

### Question

Can This Voice Thing push PCM directly into Zoom without a virtual audio device?

### What Zoom actually supports

**Yes, inside Zoom SDK-controlled experiences.**

Zoom's current Meeting SDK and Video SDK expose raw/custom audio publishing.

Examples:

- Meeting SDK Production Studio on Windows supports `sendAudio` with PCM, 32 kHz or 48 kHz, with 48 kHz recommended.
- Zoom's current Windows Meeting SDK changelog documents raw audio sender calls and requires small 10/20/30 ms chunks, with backpressure handling.
- Video SDK supports a virtual audio microphone that receives an audio sender and accepts 16-bit PCM.

References:

- https://developers.zoom.us/docs/meeting-sdk/windows/default-ui/advanced-features/production-studio-mode/
- https://developers.zoom.us/changelog/meeting-sdk/windows/7.2.1/
- https://developers.zoom.us/docs/video-sdk/windows/
- https://developers.zoom.us/docs/video-sdk/linux/raw-data/send-raw-data/
- https://developers.zoom.us/docs/meeting-sdk/windows/

The Meeting SDK is explicitly an embedded Zoom meeting/webinar experience. Zoom describes it as essentially the Zoom meeting client experience living inside the developer's application.

### Why this does not replace virtual-mic routing

The user's normal workflow is:

```text
This Voice Thing + separately running Zoom Workplace
```

The SDK workflow is:

```text
This Voice Thing embeds/hosts a Zoom SDK meeting experience
```

Those are different products.

Direct PCM therefore makes sense only if This Voice Thing intentionally becomes a Zoom meeting client/integration surface.

It does **not** simplify ordinary use enough to justify replacing the generic virtual microphone path.

### Additional constraints

Zoom's SDK has its own:

- developer credentials
- packaging/release lifecycle
- meeting/session state
- permissions/roles
- callbacks and asynchronous teardown
- media backpressure rules
- policy/usage constraints

The current Windows Meeting SDK documentation also states that the Meeting SDK is intended for human-use cases and directs AI-notetaker/realtime-media use cases toward Zoom RTMS. Live Voice is not an AI notetaker, but this reinforces that SDK-policy fit must be checked for any specialized mode rather than assumed.

### Recommendation

**NO-GO as the normal Zoom integration.**

Keep the current virtual microphone profile for standard Zoom Workplace.

**Conditional GO** only if a future product requirement specifically calls for an embedded Zoom experience, for example:

- a dedicated accessibility meeting client
- a controlled kiosk/workstation experience
- a specialized production/broadcast workflow

### Estimated effort

For a narrow SDK feasibility prototype:

- SDK packaging/credentials/session bootstrap: **1–2 weeks**
- Live Voice PCM adapter, pacing/backpressure and lifecycle: **1–2 weeks**
- production UX, policy review, auth, error states, updates: **several additional weeks**

The real cost is not sending PCM. It is owning a Zoom client surface.

## 3. Discord Social SDK

### Question

Can Discord Social SDK replace the virtual microphone and directly feed speech into the user's normal Discord desktop call?

### What Discord currently positions the SDK for

Discord's Social SDK is explicitly marketed around integrating Discord-powered social and communication features **inside games**.

Discord describes:

- in-game friends/social features
- account linking
- rich presence/game invites
- cross-platform messaging
- linked channels
- in-game Discord voice

Current communication-feature access requires an eligible game integration and review/approval. Discord's published requirements include integrating core Social SDK game features and submitting the game/integration for review.

References:

- https://discord.com/developers/social-sdk
- https://discord.com/press-releases/social-sdk-ingame-communications
- https://discord.com/blog/discords-powerful-cross-platform-chat-ready-for-your-game
- https://support-dev.discord.com/hc/en-us/articles/30127085446039-Introducing-the-Discord-Social-SDK
- https://support-dev.discord.com/hc/en-us/articles/30225844245271-Discord-Social-SDK-Terms

The SDK exposes audio-device concepts and provides Discord voice infrastructure to an integrated application/game. Discord documents it as bringing Discord communication into the game/application.

### What it does not establish

The reviewed public documentation does not establish a supported general-purpose API for:

> inject this PCM stream into the user's separately running Discord desktop client and current voice call.

That negative distinction matters.

Building an in-app Discord communications experience is not equivalent to controlling the normal Discord client.

### Product fit

This Voice Thing is not a game.

Attempting to reshape it into a Social SDK "game" integration to obtain voice functionality would create unnecessary:

- Discord application/account-linking infrastructure
- product-review/approval dependency
- SDK/platform coupling
- user-session complexity
- terms/policy risk

All to solve a problem already handled by a virtual microphone in a platform-neutral way.

### Recommendation

**NO-GO for the current desktop utility.**

Do not integrate Discord Social SDK as the route for ordinary Discord use.

Keep the Discord App Profile + virtual mic.

Revisit only if This Voice Thing later becomes embedded in a game or game-adjacent product where Discord Social SDK's native in-app communications are independently valuable.

### Estimated effort

A technical SDK bootstrap may be modest, but that is the wrong metric.

A legitimate communication integration would require:

- Discord application integration
- account/provisional-account decisions
- Social SDK core feature work
- communication-feature approval
- product UX and moderation/privacy behavior
- lifecycle/support across SDK updates

Estimate for a meaningful integration: **multiple weeks**, plus approval/policy dependency.

Because the product fit is poor, the correct estimate for the current roadmap is effectively **zero engineering allocation**.

## 4. True realtime microphone voice conversion

### Question

Can Live Voice eventually become a genuine voice changer:

```text
microphone → converted target voice → AudioRouter → virtual mic / monitor
```

rather than:

```text
microphone → ASR → text → TTS
```

### Technical viability

**Yes.**

Realtime voice-conversion projects already demonstrate the architecture.

#### RVC

The main RVC WebUI project is MIT licensed and remains active, with updates in 2026.

Repository:

- https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI

License:

- MIT

RVC is training-oriented: users generally prepare/train a target model rather than providing a few seconds of reference speech at runtime.

Strengths for This Voice Thing:

- permissive MIT code license
- established realtime voice-conversion ecosystem
- large community
- mature Windows usage
- conceptually compatible with isolated optional-engine packaging

Costs:

- voice preparation/training workflow
- separate model management from the existing zero-shot TTS voice library
- model/weights licensing must still be audited independently
- realtime capture/VAD/block scheduling becomes a new audio subsystem

#### Seed-VC / Seed-VC Realtime

Original Seed-VC provides zero-shot conversion with a 1–30 second reference and documents realtime operation. Its archived upstream reports roughly 300 ms algorithmic delay plus about 100 ms device delay for its original realtime design.

The active `jiaheguo521/seed-vc-realtime` continuation has reworked device handling, VAD and buffering. It reports measured microphone-to-speaker latency around 690 ms in one documented configuration (`block_time=0.3`, `extra_time_right=0.02`).

Repositories:

- https://github.com/Plachtaa/seed-vc
- https://github.com/jiaheguo521/seed-vc-realtime

License:

- **GPL-3.0** for both the archived upstream and the realtime continuation

The original Seed-VC repository was archived on November 21, 2025.

Strengths:

- zero-shot target voices
- naturally aligned with This Voice Thing's reference-voice concept
- existing realtime pipeline and VAD work
- no per-voice training requirement

Costs:

- GPL-3.0 distribution implications
- original upstream is archived
- current realtime continuation is a community fork
- latency is materially higher than a conventional DSP voice effect
- heavy GPU contention if TTS and conversion are expected simultaneously

### Architectural recommendation

**GO for a separate optional voice-conversion proof after the current Live Voice routing stack passes.**

Do not implement VC inside `LiveSpeechSession`.

Use a sibling source abstraction:

```text
                            ┌─ LiveSpeechSession (text/TTS)
                            │
Audio source abstraction ───┤
                            │
                            └─ LiveVoiceConversionSession (mic/VC)
                                             │
                                             ▼
                                        AudioRouter
                                      /             \
                               virtual mic        monitor
```

The router, monitoring, route profiles, Stop All, diagnostics and virtual-mic integration should be reused.

Microphone capture and conversion-specific buffering belong in the VC source/session.

### Candidate order

**First candidate: RVC**

Reason:

- MIT code license
- mature/active ecosystem
- avoids introducing GPL code into the distributable core
- good proof of the real microphone → AudioRouter architecture

The prototype should run in an **isolated optional engine environment/process**, exactly as optional TTS engines already do.

**Second research candidate: Seed-VC Realtime**

Use it as a quality/zero-shot benchmark and possible separately distributed GPL-compatible component if the product/licensing model makes that acceptable.

Do not copy/vendor GPL-3.0 Seed-VC code into the main application without an explicit licensing decision.

### Prototype acceptance criteria

A realtime VC spike should measure:

- microphone-to-monitor latency
- microphone-to-virtual-mic latency
- block size
- inference time per block
- dropouts/underruns
- GPU memory
- CPU use
- VAD behavior
- target-speaker similarity
- source linguistic intelligibility
- pitch/prosody preservation
- device hotplug behavior
- Stop latency
- feedback resilience

Initial product target:

- **< 500 ms microphone-to-output** is desirable
- **< 300 ms** would feel substantially better
- never allow inference time to routinely exceed the audio block deadline without explicit buffering behavior

These are project targets, not claims about a specific VC model.

### Estimated effort

RVC proof:

- isolated worker + model install/load + offline sample conversion: **3–5 days**
- realtime mic capture/block pipeline into AudioRouter: **3–7 additional days**
- basic UI/model/voice management: **3–5 days**
- quality/latency hardening: **1–3+ additional weeks**

Seed-VC Realtime evaluation:

- isolated technical benchmark: **3–5 days**
- distributable integration: dependent on explicit GPL/product decision

## Cross-track conclusions

### What we should build now

Continue the existing roadmap:

```text
Live TTS / cached soundboard
          ↓
      AudioRouter
      /        \
 virtual mic   monitor
      ↓
Discord / Zoom / OBS / anything
```

Validate that first.

### What we should prototype next after validation

A **true voice-conversion source** behind the existing AudioRouter, beginning with RVC.

That adds genuinely new capability rather than replacing a working generic integration with vendor-specific complexity.

### What should remain deferred

- custom signed Windows virtual microphone
- embedded Zoom meeting client
- Discord Social SDK

All three are technically possible in some form. None presently beats generic device routing on value-to-maintenance ratio.

## Decision summary

### ADR-AV1: Branded virtual microphone

**Decision: DEFER.**

Reconsider after usage/support evidence shows third-party virtual-cable setup is the dominant Live Voice adoption problem.

### ADR-AV2: Zoom PCM SDK

**Decision: NO-GO for ordinary Zoom routing.**

Virtual mic remains canonical. Embedded Zoom is a separate future product decision.

### ADR-AV3: Discord Social SDK

**Decision: NO-GO for ordinary Discord routing.**

The SDK is game/in-app communication infrastructure, not a replacement for a system microphone in the normal Discord desktop client.

### ADR-AV4: Realtime voice conversion

**Decision: GO for an isolated post-validation prototype.**

Start with RVC for permissive licensing and ecosystem maturity. Evaluate Seed-VC Realtime separately for zero-shot quality/UX, with GPL-3.0 treated as an explicit product constraint.

## Revisit triggers

Re-open the respective decisions when:

**Virtual mic**
- virtual-cable onboarding is a top support issue
- Live Voice adoption justifies driver ownership

**Zoom SDK**
- a dedicated embedded meeting experience becomes a product requirement

**Discord SDK**
- This Voice Thing becomes part of an eligible game/game integration

**Voice conversion**
- current Live Voice route/monitor/Stop/diagnostic stack passes Windows validation

## Source set reviewed

Microsoft:

- https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/sysvad-virtual-audio-device-driver-sample/
- https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/
- https://learn.microsoft.com/en-us/windows-hardware/drivers/dashboard/driver-signing-offerings
- https://learn.microsoft.com/en-gb/windows-hardware/drivers/dashboard/code-signing-reqs
- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/driver-signing

Zoom:

- https://developers.zoom.us/docs/meeting-sdk/windows/
- https://developers.zoom.us/docs/meeting-sdk/windows/default-ui/advanced-features/production-studio-mode/
- https://developers.zoom.us/changelog/meeting-sdk/windows/7.2.1/
- https://developers.zoom.us/docs/video-sdk/windows/
- https://developers.zoom.us/docs/video-sdk/linux/raw-data/send-raw-data/

Discord:

- https://discord.com/developers/social-sdk
- https://discord.com/press-releases/social-sdk-ingame-communications
- https://discord.com/blog/discords-powerful-cross-platform-chat-ready-for-your-game
- https://support-dev.discord.com/hc/en-us/articles/30127085446039-Introducing-the-Discord-Social-SDK
- https://support-dev.discord.com/hc/en-us/articles/30225844245271-Discord-Social-SDK-Terms

Voice conversion:

- https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI
- https://github.com/Plachtaa/seed-vc
- https://github.com/jiaheguo521/seed-vc-realtime
