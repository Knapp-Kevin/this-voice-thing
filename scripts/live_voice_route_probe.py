"""Standalone Live Voice audio-route probe.

Use this before involving a TTS model to verify that Qt can open a Windows audio
route, feed PCM without underruns, and identify a likely paired recording
endpoint for known virtual-cable naming patterns.

Examples:
    python scripts/live_voice_route_probe.py
    python scripts/live_voice_route_probe.py --output "CABLE Input" --seconds 3
    python scripts/live_voice_route_probe.py --output "CABLE Input" --monitor "Headphones"
"""

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaDevices

from this_voice_thing.core.live_routes import paired_input_hint, probably_virtual_device
from this_voice_thing.ui.live_audio import LiveAudioOutput


def resolve_device(devices, query):
    query = str(query or "").strip().lower()
    if not query:
        return None
    exact = [device for device in devices if device.description().lower() == query]
    if len(exact) == 1:
        return exact[0]
    matches = [device for device in devices if query in device.description().lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"No audio output matches {query!r}.")
    names = ", ".join(device.description() for device in matches)
    raise ValueError(f"Audio output {query!r} is ambiguous: {names}")


def tone_pcm(sample_rate, seconds, frequency, amplitude):
    frames = max(1, int(round(sample_rate * seconds)))
    phase = np.arange(frames, dtype=np.float64) / float(sample_rate)
    wave = np.sin(2.0 * math.pi * float(frequency) * phase)
    return np.clip(wave * float(amplitude) * 32767.0, -32768, 32767).astype("<i2").tobytes()


def list_devices(media):
    outputs = list(media.audioOutputs())
    inputs = list(media.audioInputs())

    print("Playback/output devices:")
    if not outputs:
        print("  (none)")
    for index, device in enumerate(outputs):
        hint = " [likely virtual]" if probably_virtual_device(device.description()) else ""
        preferred = device.preferredFormat()
        print(
            f"  [{index}] {device.description()}{hint} "
            f"(preferred {preferred.sampleRate()} Hz, {preferred.channelCount()} ch)"
        )

    print()
    print("Recording/input devices:")
    if not inputs:
        print("  (none)")
    for index, device in enumerate(inputs):
        print(f"  [{index}] {device.description()}")

    return outputs, inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="", help="Exact or unique substring of playback device name.")
    parser.add_argument("--monitor", default="", help="Optional exact/unique monitor playback device.")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--frequency", type=float, default=440.0)
    parser.add_argument("--amplitude", type=float, default=0.12)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", help="Print final report as JSON.")
    args = parser.parse_args()

    if args.seconds <= 0 or args.seconds > 20:
        parser.error("--seconds must be > 0 and <= 20")
    if not (20.0 <= args.frequency <= 20000.0):
        parser.error("--frequency must be between 20 and 20000 Hz")
    if not (0.0 < args.amplitude <= 0.5):
        parser.error("--amplitude must be > 0 and <= 0.5")

    app = QApplication.instance() or QApplication(sys.argv)
    media = QMediaDevices()
    outputs, inputs = list_devices(media)

    if not args.output:
        print()
        print("Pass --output with an exact name or unique substring to run the PCM route test.")
        return 0

    try:
        primary_device = resolve_device(outputs, args.output)
        monitor_device = resolve_device(outputs, args.monitor) if args.monitor else None
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if monitor_device is not None and LiveAudioOutput.device_key(monitor_device) == LiveAudioOutput.device_key(primary_device):
        print("Monitor matches primary output; monitor duplication is disabled.")
        monitor_device = None

    source_rate = 48000
    pcm = tone_pcm(source_rate, args.seconds, args.frequency, args.amplitude)

    primary = LiveAudioOutput()
    monitor = LiveAudioOutput() if monitor_device is not None else None
    failures = []
    done = {"primary": False, "monitor": monitor is None, "timeout": False}

    def maybe_finish():
        if done["primary"] and done["monitor"]:
            app.quit()

    def fail(label, message):
        failures.append(f"{label}: {message}")
        done[label] = True
        maybe_finish()

    primary.failed.connect(lambda message: fail("primary", message))
    primary.drained.connect(lambda: (done.__setitem__("primary", True), maybe_finish()))
    if monitor is not None:
        monitor.failed.connect(lambda message: fail("monitor", message))
        monitor.drained.connect(lambda: (done.__setitem__("monitor", True), maybe_finish()))

    try:
        primary.configure(primary_device, source_rate)
        if monitor is not None:
            monitor.configure(monitor_device, source_rate)
        # Feed modest chunks to exercise the same push path rather than writing
        # one giant buffer and declaring victory.
        chunk_bytes = source_rate * 2 // 10  # 100 ms mono s16
        for offset in range(0, len(pcm), chunk_bytes):
            chunk = pcm[offset:offset + chunk_bytes]
            primary.push(chunk)
            if monitor is not None:
                monitor.push(chunk)
        primary.finish_input()
        if monitor is not None:
            monitor.finish_input()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        primary.stop()
        if monitor is not None:
            monitor.stop()
        return 3

    def timed_out():
        done["timeout"] = True
        app.quit()

    QTimer.singleShot(int(args.timeout * 1000), timed_out)
    app.exec()

    input_names = [device.description() for device in inputs]
    paired = paired_input_hint(primary_device.description(), input_names)
    report = {
        "primary": {
            "device": primary_device.description(),
            "likely_virtual": probably_virtual_device(primary_device.description()),
            "paired_recording_hint": paired or None,
            "stats": primary.last_stats(),
        },
        "monitor": (
            {
                "device": monitor_device.description(),
                "stats": monitor.last_stats(),
            }
            if monitor is not None
            else None
        ),
        "tone": {
            "source_rate": source_rate,
            "seconds": args.seconds,
            "frequency": args.frequency,
            "amplitude": args.amplitude,
        },
        "failures": failures,
        "timed_out": done["timeout"],
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print()
        print("Route probe result")
        print(json.dumps(report, indent=2))

    primary.stop()
    if monitor is not None:
        monitor.stop()

    if done["timeout"]:
        return 4
    if failures:
        return 5
    if report["primary"]["stats"].get("underruns", 0):
        return 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
