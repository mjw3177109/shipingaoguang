#!/usr/bin/env python3
"""Make a 60-120 minute CPU stress fixture from a genuinely licensed SHORT video.

Real basketball pixels, REPEATED. This is never a real continuous long game.
Produces keyframe-rich 320px low-fps MP4 to keep GitHub CPU CI bounded.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path


def probe(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True, timeout=45)
    return float(json.loads(proc.stdout)["format"]["duration"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seconds", type=int, default=3600)
    p.add_argument("--source-url", required=True)
    p.add_argument("--license", required=True)
    args = p.parse_args()
    if not 3600 <= args.seconds <= 7200:
        p.error("V1.4 fixture length must be 3600..7200 seconds")
    source = Path(args.source).resolve()
    if not source.is_file() or not source.stat().st_size:
        p.error("Input clip missing")
    duration = probe(source)
    if not 1 <= duration < 600:
        p.error("Fixture source must be a short, independently licensed clip")
    destination = Path(args.out).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = time.monotonic()
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-y", "-stream_loop", "-1", "-i", str(source),
        "-t", str(args.seconds), "-map", "0:v:0",
        "-vf", "fps=2,scale=320:-2",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
        "-g", "2", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
        str(destination),
    ], check=True, timeout=480)
    resulting = probe(destination)
    if abs(resulting - args.seconds) > 0.8 or destination.stat().st_size < 10000:
        raise RuntimeError("Long stress fixture is not the specified duration")
    report = {
        "schema_version": 1, "source_type": "repeated_licensed_short_basketball",
        "continuous_real_full_game": False,
        "is_looped_fixture": True, "source_url": args.source_url,
        "license_label": args.license,
        "original_clip_seconds": duration,
        "test_duration_seconds": resulting,
        "test_input_bytes": destination.stat().st_size,
        "has_original_audio": False, "ci_low_resolution": True,
        "generation_seconds": round(time.monotonic() - before, 2),
        "disclaimer": "Repeat playback of REAL CC0 clip; no 60-minute continuous game claim.",
    }
    (destination.parent / "v14_fixture_manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
