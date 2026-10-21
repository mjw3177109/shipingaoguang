#!/usr/bin/env python3
"""Independent fail-closed V1.4 acceptance gate. Never disguises fixtures as games."""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assess(stress, *, video, fixture=None, max_elapsed=1800, max_rss_mib=2048):
    checks = {}

    def check(label, verdict, evidence):
        checks[label] = {"status": "PASS" if bool(verdict) else "FAIL", "evidence": evidence}

    kind = stress.get("test_kind")
    manifest = fixture if isinstance(fixture, dict) else {}
    check("provenance", kind in ("fixture", "real") and (
        kind == "real" and stress.get("continuous_long_game") == "operator_asserted_unverified"
        and stress.get("real_full_game_independently_verified") is False
        or kind == "fixture" and stress.get("fixture_repeats_real_short_footage") is True
        and stress.get("continuous_long_game") is False
        and manifest.get("is_looped_fixture") is True
        and manifest.get("continuous_real_full_game") is False
    ), "Repeated-source engineering test and operator-asserted real footage are distinguished")
    duration = stress.get("video_duration_seconds", 0)
    coverage = stress.get("cpu_analysis", {}).get("coverage_ratio", 0)
    scan = stress.get("cpu_analysis", {})
    check("complete_long_scan", isinstance(duration, (int, float)) and 3600 <= duration <= 7200
          and scan.get("incomplete_scan") is False and coverage >= .999
          and scan.get("frames_processed", 0) >= 1
          and scan.get("analyzed_until_seconds", 0) >= duration - 1,
          "CPU scan must reach the final second of 60-120m recording")
    check("bounded_filefield_ingest", stress.get("streaming_filefield_ingest") is True
          and stress.get("actual_long_http_upload_tested") is False
          and stress.get("input_bytes", 0) > 10000,
          "Streaming Django FileField ingestion, not full HTTP multipart benchmark")
    check("human_review_guard", stress.get("selection") == "simulated_test_approval"
          and stress.get("verified_export_snapshots") is True
          and stress.get("duplicate_queue_suppressed") is True
          and "NOT MEASURED" in stress.get("accuracy", ""),
          "Queue duplicate rejection and immutable test-approved export")
    export = stress.get("export", {})
    output_good = (video.exists() and video.stat().st_size > 5000 and
                   export.get("state") == "done" and
                   export.get("h264_aac_verified") is True and
                   export.get("output_bytes") == video.stat().st_size and
                   export.get("sha256") == digest(video) and
                   0.3 <= export.get("output_seconds", 0) <= 120)
    check("real_ffmpeg_export", output_good,
          "Actual H.264/AAC MP4, bounded duration, file size and SHA-256")
    worker = stress.get("worker", {})
    check("lease_fence_and_recovery", worker.get("heartbeat_fresh_after_scan") is True
          and worker.get("simulated_stale_lease_requeued") is True
          and worker.get("stale_worker_fenced") is True
          and worker.get("real_rerender_after_recovery") is True
          and worker.get("real_process_kill_recovery_tested") is False,
          "Simulated expired lease fenced, then real FFmpeg retry; not a SIGKILL test")
    metric = stress.get("resource_samples", {})
    rss = metric.get("peak_observed_combined_rss_bytes")
    check("rss_budget", isinstance(rss, (int, float)) and 0 < rss <= max_rss_mib * 1024**2
          and metric.get("sample_count", 0) > 0,
          "Observed worker+immediate-children RSS <= specified budget; not guaranteed peak")
    seconds = stress.get("total_elapsed_seconds", 0)
    check("elapsed_budget", isinstance(seconds, (int, float))
          and math.isfinite(seconds) and 0 < seconds <= max_elapsed,
          "Whole integration test within specified wallclock budget")
    check("resource_observations", all(isinstance(metric.get(key), int)
          and metric[key] >= 0 for key in (
             "max_observed_media_volume_used_delta_bytes",
             "max_observed_tmp_volume_used_delta_bytes")),
          "Media/tmp free-space delta measurements recorded, not physical reservations")
    result = "PASS" if all(x["status"] == "PASS" for x in checks.values()) else "FAIL"
    return {
        "schema_version": 1, "basketcut_version": "1.4",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": result, "test_kind": kind,
        "ci_engineering_fixture_only": kind == "fixture",
        "real_long_recording_verified": False,
        "test_checks": checks,
        "thresholds": {"max_elapsed_seconds": max_elapsed, "max_observed_rss_mib": max_rss_mib},
        "limitations": [
            "CI fixture is 60+ minutes of REPEATED real short basketball footage.",
            "No independently verified continuous 60-120 minute game yet.",
            "No real large-file HTTP multipart upload, parallel tenants, or proxy /tmp saturation test.",
            "Recovered simulated stale lease; did not SIGKILL a live worker.",
            "Sampled memory and disk metrics may miss spikes; disk free space is not reserved.",
            "Not a precision/recall or made-shot accuracy test.",
        ]
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--max-elapsed-seconds", type=int, default=1800)
    p.add_argument("--max-rss-mib", type=int, default=2048)
    args = p.parse_args()
    root = Path(args.out)
    stress = json.loads((root / "v14_stress_report.json").read_text(encoding="utf-8"))
    fixture_file = root / "v14_fixture_manifest.json"
    fixture = json.loads(fixture_file.read_text(encoding="utf-8")) if fixture_file.exists() else None
    report = assess(stress, video=root/"highlight.mp4", fixture=fixture,
                    max_elapsed=args.max_elapsed_seconds, max_rss_mib=args.max_rss_mib)
    (root/"v14_acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["result"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
