# Live Voice app routing

Live Voice can send speech to a normal speaker/headphone device or to the playback side of a virtual audio cable. Another application can then use the cable's paired recording endpoint as its microphone.

This is ordinary operating-system audio routing. Discord, Zoom, OBS and other applications do not need a This Voice Thing plug-in.

## The signal path

```text
Text / soundboard pad
        ↓
LiveSpeechSession
        ↓
PCM frames
        ↓
primary LiveAudioOutput
        ↓
virtual cable playback endpoint
        ↓
paired virtual cable recording endpoint
        ↓
Discord / Zoom / OBS / other app microphone input
```

An optional second `LiveAudioOutput` can monitor the same PCM through headphones. Monitor failure is intentionally isolated from the primary route.

## Route profiles

Live Voice includes:

- **Local output**: ordinary speakers or headphones.
- **External / virtual microphone**: generic external-app routing.
- **Discord**
- **Zoom**
- **OBS**
- **Other app**

Profiles remember their primary and optional monitor device IDs. External profiles start **DISARMED** on every launch. Armed state is never persisted.

That is deliberate. Restarting the app must not silently restore a route that can transmit speech into a call, meeting, stream or recording.

## Virtual cable terminology

Virtual-audio products often use confusing endpoint names. A common pattern is:

- **Playback/render side**: something like `CABLE Input`. This is where This Voice Thing sends audio.
- **Recording/capture side**: something like `CABLE Output`. This is what Discord, Zoom or another application selects as its microphone.

The wording sounds backward because each name describes the virtual device's perspective rather than the human's task. Software remains committed to tradition.

Live Voice enumerates both Windows/Qt output and input endpoints and uses conservative name matching to suggest the likely paired recording device. The hint is advisory only. Windows does not expose a universal machine-readable pairing relationship between arbitrary virtual render and capture endpoints.

## Basic setup

1. Install and configure a virtual audio cable if you do not already have one.
2. Open **Live Voice**.
3. Choose **External / virtual microphone** or an app-specific profile.
4. Under **Send voice to**, choose the virtual cable's playback endpoint.
5. If Live Voice recognizes the paired recording endpoint, it appears under **Use as microphone**. Use **Copy microphone name** if helpful.
6. Optionally enable **Also let me hear it through** and choose headphones or speakers.
7. Click **Arm external route**.
8. Click **Test route**.
9. In the target application, choose the paired recording endpoint as its microphone/input.
10. Verify that the target application's input meter responds.

Changing the primary device disarms the external route. A saved primary device that disappears is shown as unavailable; Live Voice will not fall back to system speakers.

## Discord

Current Discord desktop guidance places audio-device selection under **User Settings → Voice & Video**.

1. In This Voice Thing, choose the **Discord** route profile.
2. Select the virtual cable playback endpoint under **Send voice to**.
3. Arm the route and run **Test route**.
4. In Discord, open **User Settings → Voice & Video**.
5. Set **Input Device** to the paired recording endpoint shown by Live Voice, if available.
6. Use Discord's microphone test/input meter to confirm the route.

Discord voice processing can change synthesized speech. If the result is clipped, gated or unnaturally altered, review input sensitivity, noise suppression/Krisp and related voice-processing settings.

Official Discord reference:
https://support.discord.com/hc/en-us/articles/33030151293079-Discord-Voice-Video-Streaming-Guide

## Zoom Workplace

Current Zoom desktop settings expose microphone selection and testing under **Settings → Audio**.

1. In This Voice Thing, choose the **Zoom** route profile.
2. Select the virtual cable playback endpoint.
3. Arm the route and run **Test route**.
4. In Zoom Workplace, open **Settings → Audio**.
5. Under **Microphone**, choose the paired recording endpoint.
6. Use **Test microphone** or the input meter to verify speech is arriving.

Zoom applies microphone processing by default. If generated speech fades, pumps or loses detail, review automatic microphone volume, noise removal and other microphone-mode settings. **Original sound for musicians** is one available mode intended to reduce normal speech processing, though the best choice depends on the meeting and audio path.

Official Zoom references:
https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060612
https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0066398

## OBS Studio

On Windows, OBS can capture a microphone/device with an **Audio Input Capture** source.

1. In This Voice Thing, choose the **OBS** route profile.
2. Select the virtual cable playback endpoint.
3. Arm the route and run **Test route**.
4. In OBS, add an **Audio Input Capture** source to the desired scene.
5. Select the paired recording endpoint as the source's device.
6. Confirm the OBS mixer meter responds.

Do not also capture the same device globally under OBS audio settings unless you intentionally want it twice. OBS warns that selecting the same device both globally and as a scene source can create doubled/echoed audio.

Official OBS reference:
https://obsproject.com/kb/audio-sources

## Primary versus monitor

The primary route is authoritative. The monitor is best-effort.

If the primary device fails or disappears:
- Live Voice stops the utterance.
- The route becomes unavailable.
- External transmission does not fall back elsewhere.

If the monitor device fails or disappears:
- monitoring stops;
- the primary route continues;
- Live Voice reports the monitor failure separately.

If primary and monitor are the same device, Live Voice avoids duplicating the signal.

## Route test

**Test route** speaks:

> This Voice Thing route test.

using the current voice/model through the current route.

The test is useful for confirming:
- the selected Windows playback endpoint opens successfully;
- optional monitoring works;
- the target app sees audio on the paired recording endpoint;
- the target app is not suppressing or clipping the synthesized signal.

A successful Test route proves the current path, not every future device state. USB/headset/virtual-device changes can invalidate a route later.

## Troubleshooting

### The target app shows no microphone activity

Check, in order:

1. External route is **ARMED** in This Voice Thing.
2. **Send voice to** is the virtual cable's playback side, not its recording side.
3. The target app is using the paired recording side as its microphone/input.
4. The virtual cable endpoints are both present in Windows.
5. **Test route** produces audio or meter activity.
6. Restart or refresh the target application's audio-device list if the virtual cable was installed after the app launched.

### I hear speech locally but Discord/Zoom does not

You are probably monitoring a local device successfully while the primary external route is wrong or the target app has the wrong microphone selected. Monitoring and primary routing are separate by design.

### Discord/Zoom cuts off quiet syllables

Voice-activity detection, noise suppression, automatic gain or echo processing may be treating synthesized audio like background noise. Review the target application's microphone-processing settings.

### OBS sounds doubled or echoed

Check that the same recording endpoint is not captured both:
- as an **Audio Input Capture** scene source; and
- globally under OBS audio settings.

### My saved virtual device disappeared

Live Voice fails closed. It shows the saved endpoint as unavailable and requires you to choose a replacement. It does not silently move the route to speakers.

## What this does not do

This slice does not:
- install a virtual audio driver;
- automatically edit Discord, Zoom or OBS preferences;
- inject audio through a private application API;
- control a call's mute state;
- join voice channels or meetings;
- provide direct Discord Social SDK or Zoom Meeting SDK integration.

Those are separate integration problems. The generic virtual-microphone route is useful precisely because it works with applications that already accept ordinary microphone devices.
