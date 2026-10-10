# Live Voice QA and Performance Runbook

Status: canonical validation procedure for Live Voice.

This runbook validates the stacked Live Voice implementation from the audio transport upward. It is intentionally ordered so a failure can be isolated to model generation, Qt playback, virtual-device routing, target-application configuration, or long-session stability.

Do not call Live Voice stable until the relevant sections pass on the target Windows system.

## Targets

Initial engineering targets:

| Metric | Target |
| --- | ---: |
| Cached pad trigger to audible output | <= 100 ms |
| Warm native Live Speak first audible output | <= 500 ms |
| Stretch native first audible output | <= 300 ms |
| Sustained model RTF | < 1.0 |
| Preferred model RTF | < 0.5 |
| Audible Stop Current / Stop All | <= 150 ms |
| Sustained playback underruns | 0 |
| Route loss | fail closed |
| Monitor loss | primary route continues |

These are This Voice Thing engineering targets, not claims copied from upstream model benchmarks.

## 1. Run the unit suite

From the repo root:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Record:

- total tests
- failures/errors
- Python version
- PySide6 version

Any failure in Live Voice, worker streaming, soundboard, routing, or API tests blocks merge.

## 2. Enumerate Windows audio endpoints

Run:

```bat
.venv\Scripts\python.exe scripts\live_voice_route_probe.py
```

Confirm that:

- intended speakers/headphones are present as playback devices
- the virtual cable playback side is present
- the paired virtual recording endpoint is present
- device names shown by the probe match the Live Voice page

Save the console output with the test record.

## 3. Validate ordinary local output without TTS

Run a 3-second low-volume tone:

```bat
.venv\Scripts\python.exe scripts\live_voice_route_probe.py --output "YOUR HEADPHONES"
```

Expected:

- audible tone
- process exits successfully
- `underruns: 0`
- negotiated target format is reported
- no timeout/failure

Repeat with ordinary speakers if useful.

## 4. Validate virtual microphone routing without TTS

Run:

```bat
.venv\Scripts\python.exe scripts\live_voice_route_probe.py --output "CABLE Input" --monitor "YOUR HEADPHONES"
```

While it runs:

- select the paired recording endpoint in the target application
- verify its input meter responds
- verify the monitor path is audible
- confirm the primary probe reports zero underruns
- confirm monitor failure, if intentionally induced, does not break primary routing

For a known virtual cable the probe also reports the best-effort paired recording hint.

This step separates Windows/driver/application routing from model inference.

## 5. VoxCPM2 native Live Voice

Run this section once per **VoxCPM2 live** setting (Low latency · 6, Balanced · 8, Full quality · 10). Live Voice only; Generate always uses 10 steps. Record which setting each run used: the mode label and diagnostics (`native_timesteps`) show it.

In the app:

1. load/select VoxCPM2 and the test voice
2. choose Local output first
3. submit the standard phrase below
4. after completion, click **Copy diagnostics**

Standard phrase:

```text
This is the Live Voice native streaming validation sentence. It should begin quickly and continue without gaps while generation stays ahead of playback.
```

Record:

- generation TTFA
- RTF
- primary source rate
- negotiated target rate/channels
- configured-to-sink-start seconds
- underruns
- provenance label
- subjective boundary/artifact notes

Repeat at least 10 warm runs.

Pass:

- first audible output begins before the full utterance finishes generating
- median RTF < 1.0
- no sustained underruns
- no unexplained clicks/gaps
- provenance accurately reports native live / unwatermarked policy

## 6. Kokoro segmented Live Voice

Repeat the same standard phrase with Kokoro.

Record:

- TTFA
- RTF
- source/target sample rate
- sink start latency
- underruns
- seam/boundary quality
- provenance

Pass:

- first completed segment starts before the whole input is rendered
- subsequent sections maintain continuous-enough playback
- seam pauses sound intentional rather than broken
- provenance reports segmented/watermarked behavior

## 7. Buffered fallback

Use one compatible non-live engine.

Pass:

- UI clearly reports Buffered fallback
- speech still routes correctly
- it does not falsely imply native/segmented streaming
- Stop still clears audible output

## 8. Cached soundboard

Create a static TTS pad and let its first generation complete.

Then:

1. stop/unload the model if practical
2. trigger the cached pad repeatedly
3. measure perceived trigger-to-audio latency
4. copy diagnostics

Pass:

- cache plays without model/GPU inference
- target <= 100 ms trigger-to-audible
- no stale voice/style/provenance after cache-affecting settings change
- clearing cache preserves the pad but marks it for rebuild
- source provenance survives cached playback

## 9. Queue and shortcut stress

With Live Voice focused:

- submit multiple typed lines
- trigger pads with Alt+1…9
- switch boards with Ctrl+Alt+1…9
- use Repeat Last
- hammer one pad repeatedly until the queue limit is reached

Pass:

- queue never exceeds 25 outstanding items
- estimated outstanding duration is capped at about 10 minutes
- UI remains responsive
- queue pressure is visible
- no unbounded memory growth is observed
- shortcuts do not fire when focus is outside Live Voice

## 10. Stop latency

Test both **Stop current** and **Stop all** during:

- native generation
- segmented generation
- cached playback
- primary + monitor playback

Pass target:

- audible output is silenced <= 150 ms from user action

Inference cancellation may finish later. Record both audible stop and generation-slot release where relevant.

## 11. External route fail-closed behavior

Arm a virtual-microphone route, then remove/disable the primary virtual playback endpoint.

Pass:

- current route errors/stops
- route becomes unavailable/disarmed
- no speech falls back to speakers/default output
- user must choose/repair the destination

Restart the app.

Pass:

- external route is DISARMED after restart

## 12. Monitor failure isolation

Use:

- primary: virtual cable
- monitor: USB/headphone device

While speaking, disconnect/disable the monitor.

Pass:

- monitor stops and reports a warning
- primary route remains active
- target application continues receiving speech

If primary and monitor are accidentally identical, the app must not duplicate the audio.

## 13. Discord

Use the **Discord** profile.

Pass:

- likely paired microphone hint is correct when recognized
- Setup instructions identify the actual selected device names
- Test route drives Discord's input meter
- Live Speak and cached pads are intelligible
- Krisp/input sensitivity behavior is documented for the tested setup

Record whether Discord processing needs adjustment.

## 14. Zoom

Use the **Zoom** profile.

Pass:

- Test route drives Zoom's microphone meter
- generated voice remains intelligible
- any benefit from Original Sound / high-fidelity / suppression changes is recorded

## 15. OBS

Use the **OBS** profile.

Pass:

- Audio Input Capture receives the route
- mixer meter is stable
- no duplicated capture/echo from simultaneously capturing the same endpoint elsewhere

## 16. 30-minute soak

Run at least 30 minutes of mixed Live Voice activity:

- native speech
- segmented speech
- cached pads
- queue transitions
- board switching
- primary + monitor routing
- Stop/restart cycles

Pass:

- zero unexplained underruns
- no increasing latency
- no stuck GPU/API generation lock
- no progressive memory growth
- no lost/corrupted worker events
- no Qt audio crash/hang

A multi-hour soak should follow before calling the feature mature.

## 17. Capture diagnostics

The Live Voice **Copy diagnostics** button places a JSON snapshot on the clipboard.

It includes:

- active model metadata
- active voice kind/origin
- delivery/provenance state
- generation TTFA/RTF metrics
- route profile and Armed state
- primary and monitor device names
- source and negotiated sample rates
- sink channel count
- configured-to-start time
- bytes written
- underrun count
- queue pressure

It intentionally does not copy the text that was spoken.

Attach diagnostics to the validation record when a test fails.

## Validation record template

```text
Date:
Commit / PR:
Windows version:
GPU:
Python:
PySide6:
Virtual audio driver/version:

Unit suite:
Local route probe:
Virtual route probe:

VoxCPM2 warm TTFA median:
VoxCPM2 RTF median:
VoxCPM2 underruns:

Kokoro TTFA median:
Kokoro RTF median:
Kokoro underruns:

Cached pad latency:
Stop Current latency:
Stop All latency:

Discord:
Zoom:
OBS:

Primary disconnect:
Monitor disconnect:
30-minute soak:

Defects / notes:
Final disposition: PASS / FAIL
```

## Merge disposition

A PR may be statically complete while this validation remains incomplete.

Do not merge Live Voice to main merely because GitHub reports the branch as mergeable. The acceptance record is the evidence gate.
