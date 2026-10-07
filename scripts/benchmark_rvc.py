"""Offline block benchmark for the isolated RVC prototype.

This script does not open audio devices. It sends one mono PCM file through the
same fixed-size block protocol intended for future realtime microphone use and
reports whether inference stays ahead of each block deadline.

Example:
    python scripts/benchmark_rvc.py --install
    python scripts/benchmark_rvc.py --model C:\\voices\\target.pth \
        --index C:\\voices\\target.index --input sample.wav --output converted.wav
"""

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from this_voice_thing.engines import rvc


def mono_float(path, target_rate):
    data, source_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1).astype(np.float32, copy=False)
    if int(source_rate) == int(target_rate):
        return mono
    if not len(mono):
        return mono
    target_frames = max(1, int(round(len(mono) * target_rate / source_rate)))
    source_x = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
    target_x = np.linspace(0.0, 1.0, num=target_frames, endpoint=False)
    return np.interp(target_x, source_x, mono).astype(np.float32)


def pcm16(values):
    return (
        np.clip(np.asarray(values, dtype=np.float32), -1.0, 1.0) * 32767.0
    ).astype("<i2", copy=False).tobytes()


def percentile(values, p):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), p))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install/repair the pinned RVC prototype runtime.")
    parser.add_argument("--model", help="User-provided RVC .pth target voice model.")
    parser.add_argument("--index", default="", help="Optional matching RVC .index file.")
    parser.add_argument("--input", help="Mono/stereo source WAV/FLAC/etc. for block benchmarking.")
    parser.add_argument("--output", default="rvc_benchmark_output.wav")
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--block-ms", type=float, default=250.0)
    parser.add_argument("--crossfade-ms", type=float, default=50.0)
    parser.add_argument("--extra-ms", type=float, default=2500.0)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--formant", type=float, default=0.0)
    parser.add_argument("--index-rate", type=float, default=0.0)
    parser.add_argument("--rms-mix", type=float, default=0.5)
    parser.add_argument("--threshold", type=float, default=-60.0)
    parser.add_argument("--f0-method", choices=("rmvpe", "fcpe", "pm"), default="rmvpe")
    args = parser.parse_args()

    if args.install:
        manifest = rvc.install()
        print(json.dumps(manifest, indent=2))
        if not args.model and not args.input:
            return 0

    if not rvc.is_installed():
        parser.error("RVC prototype is not installed. Run this script once with --install.")
    if not args.model:
        parser.error("--model is required for a benchmark.")
    if not args.input:
        parser.error("--input is required for a benchmark.")

    prototype = rvc.RVCPrototype()
    try:
        loaded = prototype.load(
            args.model,
            args.index,
            sample_rate=args.sample_rate,
            block_ms=args.block_ms,
            crossfade_ms=args.crossfade_ms,
            extra_ms=args.extra_ms,
        )
        samples = mono_float(args.input, prototype.sample_rate)
        block = prototype.block_frames
        timings = []
        ratios = []
        output = []
        started = time.perf_counter()

        for offset in range(0, len(samples), block):
            values = samples[offset : offset + block]
            real_length = len(values)
            if real_length < block:
                values = np.pad(values, (0, block - real_length))
            converted = prototype.process_pcm(
                pcm16(values),
                pitch=args.pitch,
                formant=args.formant,
                index_rate=args.index_rate,
                rms_mix=args.rms_mix,
                threshold=args.threshold,
                f0_method=args.f0_method,
            )
            out = np.frombuffer(converted, dtype="<i2").astype(np.float32) / 32768.0
            output.append(out[:real_length])
            timings.append(float(prototype.last_metrics["inference_ms"]))
            ratios.append(float(prototype.last_metrics["deadline_ratio"]))

        elapsed = time.perf_counter() - started
        result = np.concatenate(output) if output else np.zeros(0, dtype=np.float32)
        sf.write(args.output, result, prototype.sample_rate, subtype="PCM_16")

        audio_seconds = len(samples) / prototype.sample_rate if prototype.sample_rate else 0.0
        report = {
            "prototype": {
                "upstream_commit": rvc.UPSTREAM_COMMIT,
                "sample_rate": prototype.sample_rate,
                "block_frames": prototype.block_frames,
                "block_ms": prototype.block_ms,
                "load": loaded,
            },
            "input": str(Path(args.input).resolve()),
            "output": str(Path(args.output).resolve()),
            "audio_seconds": round(audio_seconds, 4),
            "blocks": len(timings),
            "wall_seconds": round(elapsed, 4),
            "wall_rtf": round(elapsed / audio_seconds, 4) if audio_seconds else None,
            "inference_ms": {
                "median": round(statistics.median(timings), 3) if timings else None,
                "p95": round(percentile(timings, 95), 3) if timings else None,
                "max": round(max(timings), 3) if timings else None,
            },
            "deadline_ratio": {
                "median": round(statistics.median(ratios), 4) if ratios else None,
                "p95": round(percentile(ratios, 95), 4) if ratios else None,
                "max": round(max(ratios), 4) if ratios else None,
                "misses": sum(1 for value in ratios if value > 1.0),
            },
            "settings": {
                "pitch": args.pitch,
                "formant": args.formant,
                "index_rate": args.index_rate,
                "rms_mix": args.rms_mix,
                "threshold": args.threshold,
                "f0_method": args.f0_method,
            },
        }
        print(json.dumps(report, indent=2))
        return 0 if not report["deadline_ratio"]["misses"] else 2
    finally:
        prototype.close()


if __name__ == "__main__":
    raise SystemExit(main())
