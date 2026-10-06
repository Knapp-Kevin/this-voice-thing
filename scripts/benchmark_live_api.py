"""Measure live TTS time-to-first-audio and real-time factor over the local API.

Example:
    python scripts/benchmark_live_api.py --model "VoxCPM2 voice cloning" --voice "My Voice"

The API must already be enabled in This Voice Thing. Native streaming currently
requires VoxCPM2 and returns mono signed 16-bit little-endian PCM.
"""

import argparse
import http.client
import json
import statistics
import time
import wave
from urllib.parse import urlsplit


DEFAULT_TEXT = (
    "This is a live speech benchmark. The first sound should arrive quickly, "
    "and generation should stay comfortably ahead of playback."
)


def request_once(args):
    parsed = urlsplit(args.url)
    if parsed.scheme not in ("http", ""):
        raise ValueError("Only local HTTP URLs are supported by this benchmark.")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    base = parsed.path.rstrip("/")
    path = f"{base}/v1/audio/speech"
    payload = {
        "input": args.text,
        "stream": True,
        "stream_format": "audio",
        "response_format": "pcm",
    }
    if args.model:
        payload["model"] = args.model
    if args.voice:
        payload["voice"] = args.voice
    if args.instructions:
        payload["instructions"] = args.instructions

    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    connection = http.client.HTTPConnection(host, port, timeout=args.timeout)
    started = time.perf_counter()
    connection.request("POST", path, body=json.dumps(payload), headers=headers)
    response = connection.getresponse()
    if response.status != 200:
        message = response.read().decode("utf-8", "replace")
        connection.close()
        raise RuntimeError(f"HTTP {response.status}: {message}")

    sample_rate = int(response.getheader("X-Audio-Sample-Rate") or 0)
    sample_format = response.getheader("X-Audio-Sample-Format") or ""
    if sample_rate <= 0 or sample_format != "s16le":
        connection.close()
        raise RuntimeError(
            f"Unexpected stream metadata: rate={sample_rate!r}, format={sample_format!r}"
        )

    first = response.read1(args.read_size)
    first_audio = time.perf_counter()
    if not first:
        connection.close()
        raise RuntimeError("The stream ended before any audio arrived.")

    chunks = [first]
    while True:
        chunk = response.read1(args.read_size)
        if not chunk:
            break
        chunks.append(chunk)
    ended = time.perf_counter()
    connection.close()

    pcm = b"".join(chunks)
    audio_seconds = len(pcm) / (sample_rate * 2.0)
    wall_seconds = ended - started
    return {
        "pcm": pcm,
        "sample_rate": sample_rate,
        "ttfa": first_audio - started,
        "wall": wall_seconds,
        "audio": audio_seconds,
        "rtf": wall_seconds / audio_seconds if audio_seconds else float("inf"),
    }


def save_wav(path, result):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(result["sample_rate"])
        handle.writeframes(result["pcm"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--model", default="", help="Model label or repo ID. Blank uses the loaded model.")
    parser.add_argument("--voice", default="", help="Saved clip voice or compatible built-in voice.")
    parser.add_argument("--instructions", default="", help="VoxCPM style or voice-design instruction.")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--token", default="", help="Local API bearer token, if configured.")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--read-size", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output", default="", help="Optional WAV path for the final measured run.")
    args = parser.parse_args()

    if args.warmup < 0 or args.runs < 1:
        parser.error("--warmup must be >= 0 and --runs must be >= 1")

    for index in range(args.warmup):
        result = request_once(args)
        print(
            f"warmup {index + 1}: TTFA {result['ttfa']:.3f}s | "
            f"RTF {result['rtf']:.3f}"
        )

    results = []
    for index in range(args.runs):
        result = request_once(args)
        results.append(result)
        print(
            f"run {index + 1}: TTFA {result['ttfa']:.3f}s | "
            f"wall {result['wall']:.3f}s | audio {result['audio']:.3f}s | "
            f"RTF {result['rtf']:.3f} | {result['sample_rate']} Hz"
        )

    print()
    print(
        f"median: TTFA {statistics.median(r['ttfa'] for r in results):.3f}s | "
        f"RTF {statistics.median(r['rtf'] for r in results):.3f}"
    )
    print(
        f"best:   TTFA {min(r['ttfa'] for r in results):.3f}s | "
        f"RTF {min(r['rtf'] for r in results):.3f}"
    )

    if args.output:
        save_wav(args.output, results[-1])
        print(f"saved final run to {args.output}")


if __name__ == "__main__":
    main()
