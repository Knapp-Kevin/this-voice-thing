# Live Voice Product Specification

Status: proposed  
Product area: Live Speak + Soundboard + App Routing

## Problem

This Voice Thing can generate excellent local speech, but a completed audio file is the wrong interaction model when the user wants to communicate in real time.

Users should be able to type a thought, press one control, and have the selected voice speak directly into a live destination such as Discord or Zoom. Repeated phrases should be available instantly through a soundboard.

The product should absorb audio-routing complexity instead of requiring users to understand virtual playback endpoints, paired recording devices, buffering, model streaming modes, and conference-app audio processing.

## North Star experience

A first-time user with an already installed compatible virtual audio cable should be able to:

1. open Live Voice
2. choose a saved voice
3. choose "Discord" as the route profile
4. follow one concise prompt to select the paired microphone in Discord
5. press Test and hear/see confirmation
6. type "Give me a second, I'm checking that."
7. press Enter
8. have the sentence begin playing into Discord while generation continues
9. trigger a stored soundboard phrase without leaving the screen
10. stop everything immediately with one obvious control

No API keys, scripting, shell commands, or audio-engine vocabulary should be required.

## Primary personas

### Live communicator

Uses a synthesized voice in Discord, Zoom, game chat, or accessibility contexts.

Needs:

- low latency
- predictable routing
- clear armed state
- queue and interruption
- easy repeat phrases

### Streamer / creator

Uses voices as part of live production.

Needs:

- multiple boards
- hotkeys
- OBS routing
- monitoring
- multiple voices
- cached pads
- effects/clips

### Role-player / gamer

Uses different character voices.

Needs:

- per-pad/per-message voices
- board banks
- quick switching
- character phrase libraries
- hotkeys

### Accessibility user

Uses text as a live communication voice.

Needs:

- reliability over novelty
- keyboard-first interaction
- saved essential phrases
- low cognitive load
- emergency/priority phrases
- deterministic queue behavior

## Information architecture

Add a top-level **Live Voice** page.

Recommended page structure:

```text
Live Voice
  ├─ Speak
  ├─ Soundboard
  └─ Routing
```

Avoid creating three new sidebar destinations initially. One page with clear tabs/sections is less navigation overhead.

## Speak surface

### Header

- active voice
- active model
- route profile
- monitor state
- large Armed/Local-only indicator

### Composer

- multiline text input
- Speak button
- Enter behavior configurable
- Stop Current
- Stop All
- Clear Queue

### Queue

Each queued item shows:

- text excerpt
- voice
- destination
- state
- estimated/generated duration
- remove/reorder

### Live status

Show:

- Native / Segmented / Buffered
- Generating
- Speaking
- buffered duration
- route health
- optional TTFA diagnostic detail

Do not expose RTF prominently in normal UX. It belongs in diagnostics.

## Soundboard surface

Use a visual grid of pads.

Each pad shows:

- short label
- optional icon/color
- voice badge when it differs from board default
- cached/dynamic state
- hotkey
- small route override indicator

Pad editor:

- label
- content type
- text or clip
- voice
- model override
- style/instruction
- language
- cache behavior
- queue/interrupt policy
- route override
- hotkey
- tags

## Soundboard behaviors

### Instant static phrase

"Be right back."

Default behavior:

- render/cache in advance
- play immediately on trigger
- do not wake/load a GPU model if cache remains valid

### Dynamic phrase

"Current score is {score}."

- resolve fields
- synthesize when triggered
- display generation state

### Multi-voice board

Pads can use different voice-library entries.

Example board:

- Captain: "Hold position."
- Goblin: "Absolutely not."
- Narrator: "Meanwhile..."
- User voice: "Give me a second."

This is a major differentiator from a conventional WAV soundboard.

### Clip pad

Allow ordinary audio files too.

There is little reason to force somebody to synthesize a doorbell sound with a language model. Civilization survives another day.

## Quick phrase workflow

Any Live Speak submission can be promoted to a pad:

```text
Recent
"Give me a second, I'm checking that."

[ Repeat ] [ Save to Soundboard ]
```

Saving should preserve:

- exact text
- voice
- language
- style
- route behavior

but allow the user to rename the pad.

## Boards

Support multiple named boards.

Examples:

- Main
- Discord
- Work meetings
- D&D
- Streaming
- Accessibility
- Character: Goblin
- Character: Wizard

Board default settings:

- default voice
- route profile
- interrupt policy
- monitor behavior

Pads can override defaults.

## App profiles

An app profile is onboarding plus recommended route behavior, not a fake integration claim.

### Discord profile

Show:

- This Voice Thing output device
- Discord microphone device to choose
- Test Route
- monitoring device
- note about Krisp/noise suppression if speech sounds clipped or altered

Discord documents Krisp as local ML noise filtering and exposes configurable input behavior, so troubleshooting should account for those transformations.

### Zoom profile

Show:

- virtual device pair
- Test Route
- recommended 48 kHz route where possible
- note about noise suppression
- guidance toward Original Sound / high-fidelity mode when normal processing damages synthetic output

Zoom documents that its normal noise suppression and echo cancellation can alter full-range input and that Original Sound/high-fidelity modes reduce processing.

### OBS profile

Offer:

- direct application-audio capture guidance
- virtual-cable input guidance
- separate monitor route

OBS itself warns about duplicate device capture causing echo, so the setup assistant should explicitly detect/explain this class of configuration.

### Generic profile

For Teams, Google Meet, games, and unknown apps:

1. route This Voice Thing to a virtual cable playback device
2. select the paired recording endpoint as microphone/input in the target app
3. run Test Route

## Routing UX

Routing language should be human:

Bad:

- render endpoint
- capture endpoint
- WASAPI sink

Good:

- **Send voice to**
- **Also let me hear it through**
- **The other app should use this microphone**

Advanced diagnostics can show technical device IDs and formats.

## Arm/disarm model

External output is powerful enough to deserve an explicit state.

States:

- **Local only**: speakers/headphones, no external route
- **Armed**: submissions and soundboard triggers go to the external route
- **Muted**: route retained but external output suppressed

The title/header should show Armed prominently.

Potential later option:

- automatically arm for the current session when a known target application is running

Do not make this default until process detection and device behavior have been validated.

## Hotkeys

### MVP

- app-focused shortcuts
- emergency Stop All
- pad number shortcuts

### Later

- global hotkeys
- per-pad global hotkeys
- board switching hotkeys
- push-and-hold behavior
- Stream Deck
- MIDI

Global hotkeys require conflict handling and should be opt-in.

## History

Keep a short local session history of submitted Live Speak items.

Capabilities:

- repeat
- edit and resend
- save to soundboard
- copy
- inspect voice used

Privacy:

- history retention configurable
- easy Clear History
- do not sync by default

## Other high-value feature candidates

### Voice scenes

One click changes:

- voice
- model
- soundboard
- routing profile
- monitor
- default style

Example: "D&D Goblin on Discord."

### Emergency / priority phrases

Designated pads can interrupt current output and clear the queue.

Useful for accessibility as well as live calls.

### Random variants

A pad can choose from several equivalent lines to reduce repetition.

Example:

- "One second."
- "Give me a moment."
- "Checking now."

### Phrase variables

Templates:

- "Welcome, {name}."
- "The current score is {score}."
- "{character} says {text}."

MVP should not attempt arbitrary code or scripting. Variables are typed values.

### Clipboard Speak

Optional action to speak copied/selected text.

Useful accessibility and productivity feature.

### Repeat Last

Dedicated control and hotkey.

Extremely high value for trivial implementation cost.

### Voice favorites

Pin a few voice-library entries at the top of Live Voice.

### Preload / Keep Warm

Let a Live Voice profile optionally keep its selected model resident while the feature is armed.

Show the GPU/RAM cost clearly.

### Route test

A short built-in phrase emitted to the selected route with:

- local meter
- target device information
- optional monitor

This should be mandatory in onboarding and always available later.

### Session recorder

Optional local recording of exactly what Live Voice emitted.

Off by default.

Useful for:

- stream review
- debugging
- accessibility transcript/audio record

### Queue macros

Trigger a short ordered sequence:

1. attention sound
2. synthesized phrase
3. another clip

Not MVP.

### Sidechain/ducking

Potential streamer feature: lower background audio while Live Voice speaks.

Not MVP and should not contaminate the core speech router.

### Remote control

Phone/browser control of soundboard pads over localhost/LAN with explicit pairing.

Interesting later, not a reason to expose the main API broadly by default.

### Microphone voice conversion

True voice changer:

```text
microphone -> voice-conversion model -> live routed audio
```

Separate research track. Do not implement it by pretending ASR -> text -> TTS is equivalent.

## Accessibility requirements

- complete keyboard navigation
- visible focus states
- pad labels available to screen readers
- no color-only route/armed indication
- configurable larger soundboard pads
- hotkeys shown as text
- emergency Stop All always reachable
- speech queue can be navigated and edited by keyboard
- essential phrases can be pinned

## First-release scope

Ship:

- Live Speak
- Push to Speak
- queue
- local device playback
- virtual-device route selection
- optional headphone monitoring
- soundboard
- cached static TTS pads
- audio clip pads
- per-pad voice
- focused keyboard shortcuts
- Discord/Zoom/OBS setup profiles
- route tester
- Stop All
- history/repeat
- route and provenance diagnostics

Do not block first release on:

- custom virtual audio driver
- direct Discord SDK integration
- direct Zoom Meeting SDK integration
- global hotkeys if platform behavior is not stable
- Stream Deck/MIDI
- remote control
- live microphone voice conversion
- arbitrary scripting/macros

## Success criteria

The feature is successful when a user can go from a saved voice to intelligible speech arriving in Discord/Zoom without creating audio files manually and without understanding the underlying audio routing implementation.

The soundboard is successful when a cached static TTS pad feels as immediate as a conventional soundboard while still retaining the ability to use any stored voice.
