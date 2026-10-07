# Live Voice Workbench Architecture

Status: proposed product architecture  
Scope: desktop live speech, soundboard, audio routing, and conferencing/chat integration  
Primary platform: Windows first, with abstractions that do not unnecessarily prevent macOS/Linux support later

Tracking: umbrella issue #15. Current streaming prerequisite: issue #3 and PRs #2/#4/#5.

## 1. Product intent

This Voice Thing should treat live speech as a first-class product capability rather than an API-only feature.

The system should let a user:

- choose any compatible saved, preset, designed, or cloned voice
- type text and hear it begin speaking before the full utterance has finished rendering
- queue additional lines while speech is already playing
- save frequently used phrases as a reusable soundboard
- route generated speech to speakers, headphones, a virtual audio cable, or multiple destinations
- use a virtual audio route as the microphone source in Discord, Zoom, OBS, games, and other applications
- stop, interrupt, replace, or repeat speech predictably
- understand whether a model is using native, segmented, or buffered delivery
- see clearly when audio is armed for an external application

The product name for this subsystem is **Live Voice**. The primary text-to-speech surface is **Live Speak**. The reusable phrase/audio surface is **Soundboard**.

"Voice changer" should not be used as the technical name because that normally means microphone-to-microphone voice conversion. Live Voice is initially text-to-speech-to-audio routing.

## 2. Design principles

### 2.1 One live-generation core, multiple consumers

The desktop UI must not call the app's own HTTP API.

Both the API and the desktop experience should consume the same lower-level live speech abstraction:

```text
                        ┌─> HTTP streaming API
text / soundboard ─> LiveSpeechSession ─> AudioRouter ─> Qt audio output
                        │                    └──────────> virtual audio device
                        └─> metrics/events
```

This keeps engine behavior, cancellation, capability metadata, pronunciation handling, provenance, and latency measurements consistent.

### 2.2 Capability must be explicit

Models are classified as:

- **native**: emits waveform data before an utterance finishes, such as VoxCPM2
- **segmented**: completes short phrases/sections and yields each one while later sections continue, such as the planned Kokoro path
- **buffered**: must complete before playback

The UI should display this truthfully. "Live" is the product experience; the implementation mode remains visible in diagnostics/model metadata.

### 2.3 Virtual microphone routing is device routing first

The first integration path for Discord, Zoom, OBS, games, and arbitrary communication software is a virtual audio device, not a per-application SDK.

A virtual cable exposes a playback endpoint and paired recording endpoint:

```text
This Voice Thing
    │
    └─ writes PCM to "Virtual Cable Input" playback device
                                │
                                ▼
                       virtual audio driver
                                │
                                ▼
               "Virtual Cable Output" recording device
                                │
                   ┌────────────┼─────────────┐
                   ▼            ▼             ▼
                Discord        Zoom          OBS
                microphone     microphone     input
```

This works with applications that already know how to choose a microphone.

Microsoft documents virtual audio devices as software-backed render/capture endpoints, and WASAPI is the normal Windows mechanism for application audio flow to endpoint buffers:

- https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/virtual-audio-devices
- https://learn.microsoft.com/en-us/windows/win32/coreaudio/wasapi
- https://learn.microsoft.com/en-us/samples/microsoft/windows-driver-samples/sysvad-virtual-audio-device-driver-sample/

Qt already exposes output devices and raw streaming playback through `QMediaDevices`, `QAudioDevice`, and `QAudioSink`:

- https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaDevices.html
- https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QAudioSink.html

### 2.4 Fail closed for external routes

If a saved virtual-mic target disappears, changes ID, or cannot accept the negotiated format, Live Voice must not silently fall back to the default speakers.

External routes are intentional destinations. Missing destinations produce a visible error and leave the route disarmed.

### 2.5 Cached soundboard playback and live generation are different paths

A soundboard must feel instantaneous.

A pad may therefore be:

- **cached TTS**: phrase + voice settings with a pre-rendered audio cache
- **dynamic TTS**: phrase generated when triggered
- **audio clip**: user-provided or previously generated file
- **template**: phrase with fields resolved at trigger time, necessarily dynamic unless all values are known

Cached TTS is preferred for frequently used static phrases. Live generation is used where text, voice, style, or variables change.

## 3. Core architecture

### 3.1 LiveSpeechSession

Introduce a UI-independent service responsible for one logical live utterance.

Conceptual interface:

```python
session = LiveSpeechSession(
    model=model,
    voice=voice,
    text=text,
    language=language,
    pronunciation_dictionary=pronunciations,
    delivery_mode="auto",
)

for frame in session.frames():
    ...
```

Responsibilities:

- apply text normalization and pronunciation rules
- choose native, segmented, or buffered delivery based on declared capability
- own cancellation
- expose stream metadata before first audio when possible
- collect TTFA, RTF, chunk cadence, generated duration, underruns, and cancellation latency
- never know whether the consumer is HTTP, speakers, or a virtual device

### 3.2 AudioFrame

Use a typed audio payload rather than passing anonymous bytes between layers.

Conceptual shape:

```python
@dataclass
class AudioFrame:
    pcm: bytes
    sample_rate: int
    channels: int = 1
    sample_format: str = "s16le"
    end_of_segment: bool = False
    end_of_utterance: bool = False
    provenance: str = ""
```

The core should preserve each engine's native output rate. Sink negotiation handles conversion when necessary.

### 3.3 LiveAudioRouter

The router receives frames and fans them out to one or more sinks.

Responsibilities:

- per-sink buffering
- format negotiation
- stateful resampling when a device cannot accept the model-native format
- fan-out to monitoring and external routing
- bounded backpressure
- immediate sink shutdown on Stop
- route-level meters and health
- no implicit destination changes

Initial sinks:

1. **QtAudioSink**: local speakers/headphones or any Windows output exposed by Qt
2. **ApiStreamSink**: existing HTTP PCM consumer
3. **FileCaptureSink**: optional debugging/session capture

A virtual microphone is not a unique sink implementation. It is a `QtAudioSink` whose selected output device happens to be a virtual cable playback endpoint.

### 3.4 Qt playback

The current Player uses `QMediaPlayer`, which is file-oriented. Live Voice should use `QAudioSink`.

For the currently pinned PySide6 line, use the established `QIODevice` streaming mode. The live buffer should be represented by a small custom sequential `QIODevice` or equivalent ring-buffer-backed device.

The player should expose:

- buffered milliseconds
- underrun count
- sink state/error
- current output device
- current stream format
- volume

### 3.5 Buffering

Targets:

- initial target buffer: 150 to 300 ms
- start playback after a minimum playable threshold, not necessarily after the first tiny model chunk
- maintain enough headroom to absorb chunk jitter without adding obvious lag
- cap queued audio to prevent unbounded memory growth

The router should distinguish:

- generation queue: text waiting to synthesize
- audio buffer: already generated PCM waiting to play

These must not be one opaque queue.

## 4. Live Speak

### 4.1 Primary workflow

```text
Voice        [ Kevin Clone ▼ ]
Model        [ VoxCPM2 ▼ ]
Route        [ Discord / Virtual Mic ▼ ]
Monitor      [ Headphones ▼ ] [x]

┌───────────────────────────────────────────────┐
│ Type what you want spoken...                  │
└───────────────────────────────────────────────┘

[ Speak ]  [ Stop ]       Queue: 2
Native streaming • 0.24 s buffered • Armed
```

### 4.2 Interaction modes

**Push to Speak**

- Enter or a configurable shortcut submits the current text
- default mode
- predictable and safe

**Queue**

- new submissions can be added while an utterance is playing
- queue can be reordered, deleted, or cleared
- items display voice, route, and estimated duration

**Auto Speak**

- optional
- commits completed sentences/clauses after punctuation plus an idle debounce
- never the default
- should have a clearly visible armed state
- incomplete text remains editable

### 4.3 Interrupt policy

Per submission and per soundboard pad:

- **queue**: wait for prior speech
- **interrupt**: cancel current speech and begin this item
- **mix**: reserved for audio clips/effects, not initial TTS
- **ignore-if-busy**: useful for hotkeys that should not stack

Default text submission behavior: queue.

Default Stop behavior:

1. stop audible output immediately
2. clear the active sink buffers
3. cancel model generation cooperatively
4. optionally preserve or clear queued items according to user action

Expose two actions:

- **Stop current**
- **Stop all / clear queue**

## 5. Soundboard

### 5.1 Product concept

A soundboard is a set of pads that can emit either speech or audio through any Live Voice route.

Pads can use different voices. A single board can therefore be used for:

- personal quick phrases
- role-playing characters
- game NPC voices
- accessibility phrases
- meeting phrases
- stream production
- jokes/effects/audio clips

### 5.2 Storage

Create a dedicated `soundboard/` application directory rather than extending `voice_library/voices.json`.

Suggested layout:

```text
soundboard/
  boards.json
  cache/
    <content-hash>.wav
  clips/
    ...
```

Conceptual data model:

```json
{
  "boards": [
    {
      "id": "main",
      "name": "Main",
      "pads": [
        {
          "id": "brb",
          "label": "BRB",
          "kind": "tts",
          "text": "Be right back.",
          "voice_id": "voice-library-id",
          "model_repo_id": "",
          "language": "en",
          "style": "",
          "hotkey": "Ctrl+Alt+1",
          "interrupt_policy": "queue",
          "cache_policy": "auto",
          "route_profile": "",
          "tags": ["common"]
        }
      ]
    }
  ]
}
```

A pad should normally reference a voice by stable voice-library ID rather than copying the full voice definition.

### 5.3 Cache correctness

A static TTS pad may pre-render for instant playback.

The cache key must include at least:

- exact text
- voice ID plus voice revision/fingerprint
- engine/backend
- model repo ID and relevant model mode
- language
- style/instructions
- pronunciation-dictionary revision
- synthesis parameters that change sound
- app synthesis/cache schema version

Changing any relevant input invalidates the cache.

Do not use filenames or pad labels as cache identity.

### 5.4 Soundboard capabilities

MVP:

- multiple boards
- add/edit/delete/duplicate pads
- text pads and audio-clip pads
- voice per pad
- route per board with optional pad override
- click to trigger
- keyboard shortcut while app is focused
- cached/static versus dynamic indicator
- queue/interrupt behavior
- favorites and tags
- search/filter
- repeat last

Next:

- global hotkeys
- drag/reorder
- board banks/pages
- import/export a board
- configurable pad color/icon
- random variant group
- multi-step macros
- Stream Deck/MIDI bridge
- dynamic text templates
- session history

Later:

- remote control from phone/web UI
- collaborative board sharing
- event/webhook-triggered pads
- conditional macros

## 6. Routing profiles

Create a reusable `RouteProfile` abstraction.

Example profiles:

**Local**
- primary sink: speakers/headphones
- monitor: none

**Discord**
- primary sink: VB-CABLE Input or another virtual playback device
- monitor: headphones
- guidance: select paired cable output as Discord input

**Zoom**
- primary sink: virtual playback device
- monitor: headphones
- guidance: select paired recording endpoint as Zoom microphone

**OBS**
- primary sink: local/virtual device, depending workflow
- monitor: optional

Profiles should persist stable device IDs and display names.

If the saved ID no longer exists:

- mark profile unavailable
- do not silently substitute the system default
- offer a repair flow

## 7. Virtual audio device strategy

### 7.1 Phase 1: support existing virtual cables

Do not ship a custom kernel audio driver as part of the first Live Voice release.

Detect and support any output device exposed by Windows/Qt.

Provide first-class onboarding for common virtual cables, beginning with generic instructions and optionally tailored detection for VB-CABLE.

VB-CABLE's own documentation describes the exact desired behavior: audio written to the cable input is forwarded to its recording output. It is donationware and has distribution/licensing terms that must be respected.

Sources:

- https://vb-audio.com/Cable/index.htm
- https://vb-audio.com/Services/licensing.htm

Initial product policy:

- do not silently bundle a third-party driver
- do not require one vendor
- detect compatible virtual audio outputs generically
- provide setup guidance
- let users choose any working virtual cable

### 7.2 Phase 2+: evaluate "This Voice Thing Virtual Mic"

A branded virtual microphone would dramatically improve onboarding:

```text
Output route: This Voice Thing Virtual Mic
Discord input: This Voice Thing Mic
Zoom microphone: This Voice Thing Mic
```

However, a Windows virtual audio endpoint is a driver/distribution project, not a normal Python feature.

A feasibility spike must cover:

- SysVAD/WaveRT architecture
- signed-driver requirements
- installer elevation
- Windows driver signing/release process
- update/uninstall behavior
- crash/recovery implications
- ARM64 support
- support burden
- legal/security review

Do not make the entire Live Voice feature wait on this.

## 8. Application integrations

### 8.1 Discord

Primary support: virtual microphone route.

Discord exposes selectable input devices. Its current Social SDK also exposes input/output device APIs and voice integration, but that SDK is positioned for integrated games/apps and communications features rather than arbitrary audio injection into a user's normal Discord desktop session.

Sources:

- https://discord.com/developers/docs/social-sdk/classdiscordpp_1_1AudioDevice.html
- https://discord.com/developers/social-sdk
- https://support.discord.com/hc/en-us/articles/360040843952-Krisp-FAQ

Integration profile should provide:

- detect/select virtual cable output in This Voice Thing
- tell the user which paired recording device to select in Discord
- test phrase and level meter
- warn that aggressive noise suppression may alter synthesized audio
- troubleshooting guidance for input sensitivity/noise suppression

Do not claim a direct Discord client injection API unless Discord documents and permits one for this use case.

### 8.2 Zoom

Primary support: virtual microphone route.

Zoom's normal client can select an audio input device. Zoom's Meeting SDK can also publish custom PCM in SDK-controlled meeting experiences, including Production Studio audio, but adopting it would mean embedding a Zoom Meeting SDK client rather than controlling the user's existing Zoom desktop application.

Sources:

- https://developers.zoom.us/docs/meeting-sdk/windows/
- https://developers.zoom.us/docs/meeting-sdk/windows/default-ui/advanced-features/production-studio-mode/
- https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0059985

Integration profile should provide:

- virtual microphone setup
- test phrase and meter
- guidance on Zoom noise suppression
- suggest Original Sound / appropriate audio profile when Zoom processing audibly damages TTS
- never change Zoom settings automatically without an explicit supported integration

A future direct Zoom SDK spike is valid for specialized use cases but should not block the generic route.

### 8.3 OBS

OBS can capture audio input/output devices and can also capture per-application audio on modern Windows.

Sources:

- https://obsproject.com/kb/audio-sources
- https://obsproject.com/kb/application-audio-capture-guide

Support both:

- direct application-audio capture where suitable
- virtual-cable routing for workflows that need a dedicated source

### 8.4 Other apps

Teams, Google Meet, games, browser calls, DAWs, streaming tools, and accessibility software should work through the same device-routing mechanism when they can select a microphone/input device.

The product should therefore say **App Profiles**, not hard-code architecture around Discord and Zoom.

## 9. Monitoring

Users often need to hear what the other application is receiving.

Support optional simultaneous monitoring:

```text
LiveSpeechSession
       │
       ▼
  AudioRouter
   ├─> Virtual Mic
   └─> Headphones
```

Monitoring defaults:

- off for speakers when a physical microphone may pick the output back up
- headphones recommended
- warn about feedback loops
- independent monitor volume

Multi-sink fan-out must not allow a slow sink to block all other sinks. Each sink needs bounded buffering and independent error handling.

## 10. App profile assistant

Add a small setup assistant rather than forcing users to understand Windows audio topology.

Example:

```text
Discord setup

1. Output from This Voice Thing
   ✓ CABLE Input (VB-Audio Virtual Cable)

2. Discord microphone
   Select: CABLE Output (VB-Audio Virtual Cable)

3. Monitoring
   ✓ Headphones (Realtek USB)

[ Test route ]

Input heard by virtual mic: ✓
Monitoring: ✓
```

The app cannot reliably manipulate another application's settings without an explicit supported API, so instructions should be clear and exact rather than pretending automation exists.

## 11. State model

Live Voice should expose an explicit state machine:

```text
DISARMED
   ↓ arm route
READY
   ↓ submit
GENERATING
   ↓ first playable buffer
PLAYING
   ↔ GENERATING (overlap)
   ↓ current utterance ends
DRAINING
   ↓ buffer empty
READY

Any active state ── Stop ──> STOPPING ──> READY
Any state ── route/device failure ──> ERROR
```

External routing gets a prominent **ARMED** indicator.

Closing the app or switching route profiles must disarm external routing.

## 12. Safety and provenance

Live Voice is capable of speaking into real calls, so accidental emission matters.

Required controls:

- external routes start disarmed after app launch unless the user explicitly enables a trusted-session preference later
- persistent visible route/armed indicator
- one-click emergency Stop All
- configurable global emergency-stop hotkey
- fail closed on missing output device
- no automatic fallback from virtual mic to physical speakers
- queue length cap
- clear indication of active voice
- provenance/watermark status available in diagnostics
- preserve engine watermarking where stream-safe
- never silently claim a watermark when the live path does not apply one

The app should not market synthetic speech as indistinguishable identity proof. Saved voices need clear names/origin metadata.

## 13. Performance targets

Targets are measured on target hardware, not guarantees copied from upstream benchmarks.

**Cached soundboard pad**
- trigger to first audible sample: target <= 100 ms
- no GPU required when a valid cache exists

**Warm native Live Speak**
- submit to first audible speech: target <= 500 ms
- stretch: <= 300 ms

**Segmented Live Speak**
- submit to first audible speech: target <= 750 ms initially
- tune live-section size from actual measurements

**Queue transition**
- gap between cached/ready items: target <= 150 ms unless an intentional seam pause applies

**Stop**
- user action to local silence: target <= 150 ms
- generation cancellation may complete later, but must not continue audible playback

**Underruns**
- zero underruns during sustained playback on a supported model/profile under normal load

## 14. Persistence

Suggested files:

```text
voice_library/
  voices.json

soundboard/
  boards.json
  cache/
  clips/

app_settings.json
  live_voice:
    active_board
    default_route_profile
    monitor_volume
    queue_policy
    emergency_stop_hotkey
  route_profiles:
    ...
```

Soundboard and route-profile schemas need explicit version numbers and migrations.

## 15. Implementation order

### Gate 0: finish current streaming foundation

Depends on the current live-TTS work:

- native PCM path validated
- segmented fallback validated
- cancellation validated
- TTFA/RTF benchmark evidence recorded

### Slice 1: local live playback

- `AudioFrame`
- `LiveSpeechSession`
- `LiveAudioRouter`
- `QAudioSink` implementation
- speakers/headphones device selection
- buffer/underrun metrics
- Stop All

### Slice 2: Live Speak UI

- text box
- Push to Speak
- queue
- route selector
- monitor selector
- live state/meter
- stop/clear controls

### Slice 3: Soundboard

- persistence
- text/audio pads
- voice references
- cached rendering
- invalidation
- pad click/keyboard trigger
- queue/interrupt policy

### Slice 4: virtual microphone routing

- generic output-device route profiles
- virtual-cable detection
- dual routing to virtual device + headphones
- route test assistant
- fail-closed device persistence

### Slice 5: app profiles

- Discord
- Zoom
- OBS
- generic microphone application
- per-profile troubleshooting

### Slice 6: interaction expansion

- global hotkeys
- board banks
- import/export
- quick history/repeat
- Stream Deck/MIDI investigation
- dynamic templates

### Slice 7: hardening

- long-session soak tests
- device unplug/replug
- model switch while queued
- crash recovery
- audio feedback tests
- cache corruption recovery
- accessibility/keyboard navigation

### Slice 8: advanced integration research

Independent spikes:

- branded This Voice Thing virtual microphone driver
- Zoom Meeting SDK direct PCM publishing
- Discord Social SDK suitability
- microphone-to-microphone realtime voice conversion

None of these advanced spikes blocks the core product.

## 16. Architectural decision

**Decision:** Build Live Voice around a shared live-generation service and generic audio-device routing. Use virtual microphone devices as the primary cross-application integration mechanism. Treat direct Discord/Zoom SDK integration and a custom virtual audio driver as later optional paths.

This provides the broadest compatibility with the least platform coupling while reusing the streaming work already underway.
