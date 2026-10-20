#!/usr/bin/env python3
"""Create a bounded 75s repeated-playback fixture from a licensed REAL source clip.

This is NOT 75 seconds of original continuous game recording.
The returned content is purposely lower resolution for free GitHub CPU runners.
"""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path


def media_info(path):
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)], check=True, capture_output=True,
        text=True, timeout=30)
    return float(json.loads(output.stdout)["format"]["duration"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seconds", type=int, default=75)
    p.add_argument("--source-url", required=True)
    p.add_argument("--license", required=True)
    args = p.parse_args()
    if not 60 <= args.seconds <= 600:
        p.error("Fixture duration must be within 60-600 seconds")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        p.error("FFmpeg and FFprobe must be installed")
    source = Path(args.source).resolve()
    output = Path(args.out).resolve()
    if not source.is_file() or source.stat().st_size < 10000:
        p.error("Licensed source clip is missing or too small")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_seconds = media_info(source)
    if not 1 <= source_seconds <= 3600:
        p.error("Invalid source duration")
    start = time.monotonic()
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-stream_loop", "-1", "-i", str(source), "-t", str(args.seconds),
        "-map", "0:v:0", "-vf", "fps=8,scale=640:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
        str(output)], check=True, timeout=240)
    produced = media_info(output)
    if not abs(produced - args.seconds) < 0.6 or output.stat().st_size < 10000:
        raise RuntimeError("Repeated fixture duration/size check failed")
    report = {
        "schema_version": 1, "type": "looped_licensed_basketball_stress_fixture",
        "source_url": args.source_url, "source_license": args.license,
        "source_duration_seconds": source_seconds,
        "fixture_duration_seconds": produced,
        "continuous_unedited_game": False,
        "source_repetition": True,
        "purpose": "Engineering upload/CPU/export stress; NOT sports recognition accuracy.",
        "fixture_bytes": output.stat().st_size,
        "creation_elapsed_seconds": round(time.monotonic()-start, 2),
    }
    (output.parent/"fixture_manifest.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
