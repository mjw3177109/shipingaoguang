#!/usr/bin/env python3
"""Fail-closed audit of actual V1.3 Django upload, worker and FFmpeg artifacts."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def duration_and_codec(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=40)
    return json.loads(result.stdout)


def verify(args):
    root = Path(args.out).resolve()
    smoke = json.loads((root/"report.json").read_text(encoding="utf-8"))
    edl = json.loads((root/"edit_list.json").read_text(encoding="utf-8"))
    fixture = json.loads((root/"fixture_manifest.json").read_text(encoding="utf-8"))
    preflight = json.loads((root/"diagnostics.json").read_text(encoding="utf-8"))
    source = Path(args.fixture)
    rendered = root/"highlight.mp4"
    findings = {}

    def test(name, ok, details):
        findings[name] = {"passed": bool(ok), "details": details}

    test("preflight", preflight.get("overall") == "PASS"
         and not preflight.get("failed_checks")
         and all(preflight["checks"][k]["status"] == "PASS"
                 for k in ("database", "migrations", "media_directory", "media_disk",
                           "worker", "queue", "ffmpeg", "ffprobe", "resource_limits")),
         "Database, migrations, Worker, queue, FFmpeg, disk and quota checks")
    test("licensed_input", fixture.get("source_repetition") is True
         and fixture.get("continuous_unedited_game") is False
         and fixture.get("fixture_duration_seconds", 0) >= 74.4
         and fixture.get("source_license") and fixture.get("source_url"),
         "75s looped real licensed basketball video; not an unedited 75s match")
    test("upload_and_cpu", smoke.get("original_duration_seconds", 0) >= 74.4
         and isinstance(smoke.get("cpu_candidate_count"), int)
         and smoke.get("source_sha256") == digest(source),
         "Real Django upload, CPU Worker analysis and source fingerprint")
    test("review_snapshot", smoke.get("immutable_export_plan_verified") is True
         and smoke.get("queued_duplicate_suppressed") is True
         and smoke.get("export_plan_sha256") == edl.get("plan_sha256")
         and edl.get("plan", {}).get("human_review_required") is True
         and edl.get("plan", {}).get("automatic_scoring_claim") is False
         and smoke.get("review_in_test", "").startswith("automated"),
         "Simulated test reviewer; immutable reviewed export plan and de-duplication")
    test("ffmpeg_export", smoke.get("export_success") is True
         and smoke.get("clips_completed") == 1
         and rendered.exists() and rendered.stat().st_size > 5000
         and smoke.get("output_sha256") == digest(rendered),
         "Nonempty FFmpeg output hash, clip counter and saved MP4")
    if rendered.exists():
        info = duration_and_codec(rendered)
        streams = info.get("streams", [])
        output_seconds = float(info.get("format", {}).get("duration", 0))
        test("playable_mp4", 0.5 <= output_seconds <= 120
             and any(stream.get("codec_type") == "video"
                     and stream.get("codec_name") == "h264" for stream in streams)
             and any(stream.get("codec_type") == "audio"
                     and stream.get("codec_name") == "aac" for stream in streams),
             "Playable H.264/AAC export verified by FFprobe")
    else:
        test("playable_mp4", False, "Exported MP4 missing")

    test("honest_scope", "NOT MEASURED" in smoke.get("accuracy", "")
         and smoke.get("export_human_review_in_test", "").startswith("SIMULATED"),
         "No claims of real shot accuracy, expert review or full-game throughput")

    passed = all(item["passed"] for item in findings.values())
    report = {
        "schema_version": 1, "basketcut_version": "1.3",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall": "PASS" if passed else "FAIL",
        "checks": findings,
        "provenance": {"source_url": fixture["source_url"],
                       "license": fixture["source_license"],
                       "source_repetition": True},
        "limits": [
            "75-second CPU integration sample uses repeated real footage.",
            "Simulated reviewer only; sports recognition accuracy NOT MEASURED.",
            "CI performance is not production throughput or 1-6 hour stress verification.",
            "Host/container disk concurrency and HTTP reverse-proxy upload limits not load-tested.",
        ],
    }
    (root/"acceptance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return passed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--fixture", required=True)
    args = p.parse_args()
    if not verify(args):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
