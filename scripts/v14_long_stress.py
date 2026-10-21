#!/usr/bin/env python3
"""V1.4 isolated long-recording Worker/FFmpeg stress; no whole-file buffering.

Uses a streaming FileField ingestion, NOT a full HTTP multipart upload test.
DANGEROUS BY DESIGN to use on a real DB: requires isolated SQLite and explicit opt-in.
Only simulated worker expiry is tested, not process SIGKILL/OS crash recovery.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import timedelta, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basketcut.settings")

import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone
from highlights.management.commands.runworker import Command as WorkerCommand
import highlights.management.commands.runworker as worker_module
from highlights.models import Candidate, Export, Game, Venue
from highlights.operations import require_media_space, owner_raw_media_bytes
from highlights.services import probe_duration
from highlights.worker_health import worker_is_alive
from highlights.worker_leases import WorkerLeaseLost, heartbeat_export


def _ffprobe(path):
    p = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,codec_name",
        "-of", "json", str(path)], capture_output=True, text=True,
        check=True, timeout=90)
    return json.loads(p.stdout)


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _rss_bytes(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


def _child_pids(pid):
    try:
        return [int(p) for p in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
    except (OSError, ValueError):
        return []


class ResourceMonitor:
    """Best-effort sampled RSS and volume consumption, including immediate children."""
    def __init__(self, media_path):
        self.root = media_path
        self.temporary = Path(__import__("tempfile").gettempdir())
        self.stop = threading.Event()
        self.peak_self_rss = 0
        self.peak_children_rss = 0
        self.peak_combined_rss = 0
        self.media_free_initial = shutil.disk_usage(media_path).free
        self.tmp_free_initial = shutil.disk_usage(self.temporary).free
        self.min_media_free = self.media_free_initial
        self.min_tmp_free = self.tmp_free_initial
        self.samples = 0
        self.thread = threading.Thread(target=self._sample_forever, daemon=True)

    def _sample_forever(self):
        pid = os.getpid()
        while not self.stop.is_set():
            try:
                mine = _rss_bytes(pid)
                children = sum(_rss_bytes(ch) for ch in _child_pids(pid))
                self.peak_self_rss = max(mine, self.peak_self_rss)
                self.peak_children_rss = max(children, self.peak_children_rss)
                self.peak_combined_rss = max(mine + children, self.peak_combined_rss)
                self.min_media_free = min(self.min_media_free, shutil.disk_usage(self.root).free)
                self.min_tmp_free = min(self.min_tmp_free, shutil.disk_usage(self.temporary).free)
                self.samples += 1
            except OSError:
                pass
            self.stop.wait(.20)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        return {
            "sample_interval_seconds": .2,
            "sample_count": self.samples,
            "peak_python_worker_rss_bytes": self.peak_self_rss or None,
            "peak_immediate_child_rss_bytes": self.peak_children_rss or None,
            "peak_observed_combined_rss_bytes": self.peak_combined_rss or None,
            "max_observed_media_volume_used_delta_bytes":
                max(0, self.media_free_initial - self.min_media_free),
            "max_observed_tmp_volume_used_delta_bytes":
                max(0, self.tmp_free_initial - self.min_tmp_free),
            "limitations": "Sampled Linux /proc RSS; short spikes may be missed. "
                           "Disk figures are free-space deltas, not job-only accounting; "
                           "MEDIA_ROOT and /tmp may share one filesystem.",
        }


def run(args):
    if os.getenv("BASKETCUT_V14_ISOLATED") != "YES" or not args.confirm_isolated:
        raise RuntimeError("Requires BASKETCUT_V14_ISOLATED=YES and --confirm-isolated")
    if not settings.DEBUG or connection.vendor != "sqlite":
        raise RuntimeError("Refusing V1.4 stress test outside DEBUG + isolated SQLite")
    source = Path(args.video).resolve()
    result_root = Path(args.out).resolve()
    media_root = Path(settings.MEDIA_ROOT).resolve()
    if not source.is_file() or not 3600 <= probe_duration(source) <= 7200:
        raise RuntimeError("Input must be an existing decodable 60-120 minute recording")
    if source == media_root or media_root in source.parents:
        raise RuntimeError("Input must not be located inside MEDIA_ROOT")
    if not media_root.is_dir() or not result_root.is_dir():
        raise RuntimeError("Create isolated MEDIA_ROOT and output directories first")
    dbname = connection.settings_dict.get("NAME", "")
    if not dbname or Path(str(dbname)).resolve() == (Path(settings.BASE_DIR) / "db.sqlite3").resolve():
        raise RuntimeError("Refusing default development SQLite DB; configure a separate temporary DATABASE_URL")
    if result_root == media_root or media_root in result_root.parents:
        raise RuntimeError("Audit output must be outside MEDIA_ROOT")
    size = source.stat().st_size
    if not (0 < size <= settings.MAX_UPLOAD_BYTES and size <= settings.OWNER_MAX_RAW_BYTES):
        raise RuntimeError("Source exceeds configured upload or account quota")
    if args.kind == "real":
        if not args.source_url or not args.license:
            raise RuntimeError("Real recording requires --source-url and --license labels")
        if (source.parent / "v14_fixture_manifest.json").exists():
            raise RuntimeError("Detected repeated-content fixture; cannot label as real game")
    else:
        manifest = json.loads((source.parent / "v14_fixture_manifest.json").read_text())
        if not (manifest.get("is_looped_fixture") is True
                and manifest.get("continuous_real_full_game") is False):
            raise RuntimeError("Fixture provenance manifest is missing or inconsistent")

    monitor = ResourceMonitor(media_root)
    started = time.monotonic()
    stages = {}
    report = None
    user = None
    game = None
    monitor.start()
    try:
        user = get_user_model().objects.create_user(username="v14_" + uuid.uuid4().hex[:14])
        venue = Venue.objects.create(owner=user, name="V14 isolated test venue")
        t = time.monotonic()
        require_media_space(size)
        if owner_raw_media_bytes(user) != 0:
            raise RuntimeError("Isolated user has unexpected pre-existing media")
        with source.open("rb") as fp:
            game = Game.objects.create(
                venue=venue, title="V14 isolated long video",
                duration_seconds=0, video=File(fp, name=source.name))
        stages["stream_filefield_ingest_seconds"] = round(time.monotonic() - t, 3)
        if game.video.size != size or owner_raw_media_bytes(user) != size:
            raise RuntimeError("Stored media size or quota accounting mismatch")
        game.duration_seconds = probe_duration(game.video.path)
        game.analysis_engine = "cpu"
        game.state = Game.State.QUEUED
        game.save(update_fields=["duration_seconds", "analysis_engine", "state"])

        worker = WorkerCommand()
        telemetry = {}
        original_scan = worker_module.scan_cpu_video

        def scan_instrumentation(*scan_args, **scan_kwargs):
            result = original_scan(*scan_args, **scan_kwargs)
            telemetry.update(result["analysis"])
            return result

        t = time.monotonic()
        with patch.object(worker_module, "scan_cpu_video", scan_instrumentation):
            if not worker._process_one():
                raise RuntimeError("Worker could not claim long CPU analysis")
        stages["worker_cpu_analysis_seconds"] = round(time.monotonic() - t, 3)
        game.refresh_from_db()
        if game.state != Game.State.READY:
            raise RuntimeError("CPU analysis failed or aborted")
        if not telemetry or telemetry.get("incomplete_scan") or telemetry.get("coverage_ratio", 0) < .999:
            raise RuntimeError("Scan did not cover the complete 60-120 minute timeline")
        online, _age = worker_is_alive()
        if not online:
            raise RuntimeError("Worker heartbeat missing after analysis")

        candidates = list(game.candidates.filter(source__in=[
            "cpu_ball_activity", "cpu_hoop_activity"]).order_by("start_seconds")[:1])
        candidate_count = game.candidates.count()
        if candidates:
            selected = candidates[0]
        else:
            selected = Candidate.objects.create(
                game=game, start_seconds=1, end_seconds=6,
                source="manual", note="V14 simulated review fallback; not shot accuracy")
        selected.review = Candidate.Review.APPROVED
        selected.save(update_fields=["review"])

        client = Client(HTTP_HOST="localhost")
        client.force_login(user)
        t = time.monotonic()
        target = reverse("queue_export", args=[game.pk])
        for _ in range(2):
            response = client.post(target, {"player_label": "", "audio_mode": "original"})
            if response.status_code != 302:
                raise RuntimeError("Export queue endpoint not successful")
        jobs = list(Export.objects.filter(game=game))
        if len(jobs) != 1:
            raise RuntimeError("Duplicate export request was not suppressed")
        first = jobs[0]
        if not first.plan or not first.plan_sha256 or first.plan.get("clip_count") != 1:
            raise RuntimeError("Immutable human-review snapshot missing")
        stages["two_export_queue_requests_seconds"] = round(time.monotonic() - t, 3)

        t = time.monotonic()
        if not worker._process_one():
            raise RuntimeError("Worker could not claim first FFmpeg export")
        stages["ffmpeg_export_seconds"] = round(time.monotonic() - t, 3)
        first.refresh_from_db()
        if first.state != Export.State.DONE or not first.video:
            raise RuntimeError("FFmpeg export failed")
        delivered = result_root / "highlight.mp4"
        shutil.copyfile(first.video.path, delivered)
        output_info = _ffprobe(delivered)
        streams = output_info.get("streams", [])
        if not (any(s.get("codec_type") == "video" and s.get("codec_name") == "h264"
                    for s in streams)
                and any(s.get("codec_type") == "audio" and s.get("codec_name") == "aac"
                        for s in streams)):
            raise RuntimeError("Export missing H.264 video or AAC audio")

        # Simulation is deliberate: no OS process is killed in this test.
        # Requeue a fake expired export lease, fence old token, then ACTUALLY rerender.
        old = dj_timezone.now() - timedelta(hours=2)
        again = Export.objects.create(
            game=game, state=Export.State.RUNNING,
            audio_mode="original", plan=first.plan,
            plan_sha256=first.plan_sha256, clips_total=1,
            worker_started_at=old, worker_heartbeat_at=old,
            worker_token="v14-expired-worker",
        )
        t = time.monotonic()
        recovery = worker._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        again.refresh_from_db()
        if (recovery["export"] != 1 or again.state != Export.State.QUEUED
                or again.worker_recovery_count != 1):
            raise RuntimeError("Expired export task was not requeued")
        old_token_fenced = False
        try:
            heartbeat_export(again.pk, "v14-expired-worker")
        except WorkerLeaseLost:
            old_token_fenced = True
        if not old_token_fenced:
            raise RuntimeError("Expired worker token still allowed to mutate the queue")
        if not worker._process_one():
            raise RuntimeError("Recovered export not claimed")
        again.refresh_from_db()
        if again.state != Export.State.DONE or not again.video:
            raise RuntimeError("Recovered export failed real rendering")
        stages["simulated_lease_recovery_and_real_rerender_seconds"] = round(
            time.monotonic() - t, 3)

        monitor_metrics = monitor.close()
        report = {
            "schema_version": 1, "basketcut_version": "1.4",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "test_kind": args.kind,
            "fixture_repeats_real_short_footage": args.kind == "fixture",
            "continuous_long_game": False if args.kind == "fixture" else "operator_asserted_unverified",
            "real_full_game_independently_verified": False,
            "source_url_label": args.source_url or "",
            "license_label": args.license or "",
            "video_duration_seconds": game.duration_seconds,
            "input_bytes": size,
            "streaming_filefield_ingest": True,
            "actual_long_http_upload_tested": False,
            "cpu_analysis": telemetry,
            "cpu_candidate_count": candidate_count,
            "selection": "simulated_test_approval",
            "manual_fallback_used": not candidates,
            "verified_export_snapshots": True,
            "duplicate_queue_suppressed": True,
            "export": {
                "state": first.state,
                "output_seconds": float(output_info["format"]["duration"]),
                "output_bytes": delivered.stat().st_size,
                "sha256": _sha256(delivered),
                "h264_aac_verified": True,
                "audio_fallback_used": first.audio_fallback_used,
            },
            "worker": {
                "heartbeat_fresh_after_scan": online,
                "simulated_stale_lease_requeued": True,
                "stale_worker_fenced": old_token_fenced,
                "real_rerender_after_recovery": True,
                "real_process_kill_recovery_tested": False,
            },
            "resource_samples": monitor_metrics,
            "stage_elapsed_seconds": stages,
            "total_elapsed_seconds": round(time.monotonic() - started, 3),
            "accuracy": "NOT MEASURED - no independent annotations",
            "disclaimer": "60m+ engineering test; no HTTP large-upload, physical-quota, concurrent-load or sports accuracy claim.",
        }
        return report
    finally:
        if monitor.thread.is_alive():
            monitor.close()
        # Safe because this script only runs against explicitly isolated SQLite.
        if game is not None:
            for job in game.exports.all():
                if job.video:
                    job.video.delete(save=False)
            if game.video:
                game.video.delete(save=False)
        if user is not None:
            user.delete()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--kind", choices=("fixture", "real"), required=True)
    p.add_argument("--source-url", default="")
    p.add_argument("--license", default="")
    p.add_argument("--confirm-isolated", action="store_true")
    args = p.parse_args()
    dest = Path(args.out).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        report = run(args)
        (dest / "v14_stress_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "result": "MEASURED",
            "duration_seconds": report["video_duration_seconds"],
            "cpu_scan_coverage": report["cpu_analysis"]["coverage_ratio"],
            "total_elapsed_seconds": report["total_elapsed_seconds"]
        }, ensure_ascii=False, indent=2))
    except Exception as exc:
        failure = {
            "schema_version": 1, "basketcut_version": "1.4",
            "result": "FAIL",
            "error_type": type(exc).__name__,
            "detail": "Stress execution failed; inspect CI logs or rerun in an isolated environment.",
            "accuracy": "NOT MEASURED",
        }
        (dest / "v14_stress_failure.json").write_text(json.dumps(failure, indent=2))
        raise


if __name__ == "__main__":
    main()
