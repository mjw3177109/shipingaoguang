#!/usr/bin/env python3
"""Actual SIGKILL of CPU Worker process-group during live FFmpeg export.

Then wait for lease expiry and start a NEW real Worker to recover/re-render.
Must only run against an explicitly isolated temporary CI SQLite instance.
"""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basketcut.settings")
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import close_old_connections
from highlights.export_plan import create_plan
from highlights.models import Candidate, Export, Game, Venue
from highlights.services import probe_duration


def inspect(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-of", "json",
                           "-show_entries", "format=duration:stream=codec_type,codec_name",
                           str(path)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(proc.stdout)


def children_of(pid):
    try:
        return [int(s) for s in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]
    except OSError:
        return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    if os.environ.get("BASKETCUT_V15_ISOLATED") != "YES":
        raise RuntimeError("Refusing SIGKILL test without explicit V1.5 isolation")
    if not settings.DEBUG or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("Refusing production database")
    if "basketcut-v15" not in str(settings.DATABASES["default"]["NAME"]):
        raise RuntimeError("Dedicated V1.5 database required")
    src = Path(args.video).resolve()
    target = Path(args.out).resolve()
    target.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(src)
    if duration < 12:
        raise RuntimeError("Real FFmpeg source needs >=12 sec to allow SIGKILL mid-render")

    user = get_user_model().objects.create_user(username="v15_kill_" + uuid.uuid4().hex[:12])
    venue = Venue.objects.create(owner=user, name="V15 crash test")
    with src.open("rb") as fp:
        game = Game.objects.create(
            venue=venue, title="V15 live SIGKILL test", state=Game.State.READY,
            duration_seconds=duration, video=File(fp, name="kill_fixture.mp4"))
    clip = Candidate.objects.create(
        game=game, start_seconds=.2, end_seconds=min(duration-.2, 60),
        review="approved", source="manual")
    plan, digest = create_plan(game, "")
    job = Export.objects.create(game=game, audio_mode="silent",
                                plan=plan, plan_sha256=digest, clips_total=1)
    log = (target / "v15_worker_crash.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, "manage.py", "runworker", "--once", "--stale-after", "1"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    observed_child = False
    observed_running = False
    started = time.monotonic()
    try:
        while time.monotonic() - started < 35:
            close_old_connections()
            job.refresh_from_db()
            if job.state == Export.State.RUNNING and job.worker_token:
                observed_running = True
                if children_of(proc.pid):
                    observed_child = True
                    break
            if proc.poll() is not None:
                break
            time.sleep(.04)
        if not observed_running or not observed_child or proc.poll() is not None:
            raise RuntimeError("Did not observe live FFmpeg subprocess under RUNNING Worker")
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=15)
        job.refresh_from_db()
        if job.state != Export.State.RUNNING:
            raise RuntimeError("Worker was not killed during a live unfinished export")
        old_token = job.worker_token
        time.sleep(2.3)
        close_old_connections()
        with (target / "v15_replacement_worker.log").open("w") as runner_log:
            recovered = subprocess.run(
                [sys.executable, "manage.py", "runworker", "--once",
                 "--stale-after", "1", "--max-recoveries", "2"],
                stdout=runner_log, stderr=subprocess.STDOUT, timeout=220)
        if recovered.returncode != 0:
            raise RuntimeError("Replacement Worker did not exit successfully")
        job.refresh_from_db()
        if (job.state != Export.State.DONE or not job.video
                or job.worker_recovery_count != 1
                or job.worker_token):
            raise RuntimeError("Lease reclaim/actual FFmpeg re-render did not complete")
        from highlights.worker_leases import WorkerLeaseLost, heartbeat_export
        fenced = False
        try:
            heartbeat_export(job.pk, old_token)
        except WorkerLeaseLost:
            fenced = True
        if not fenced:
            raise RuntimeError("Killed Worker's stale token remained writable")
        info = inspect(job.video.path)
        streams = info.get("streams", [])
        if not any(v.get("codec_type") == "video" and v.get("codec_name") == "h264"
                   for v in streams):
            raise RuntimeError("Recovered output is not H264")
        output = target/"v15_recovered_highlight.mp4"
        import shutil
        shutil.copyfile(job.video.path, output)
        h = hashlib.sha256(output.read_bytes()).hexdigest()
        report = {
            "schema_version": 1, "basketcut_version": "1.5",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "result": "PASS",
            "real_os_sigkill_of_worker_process_group": True,
            "ffmpeg_child_observed_before_kill": observed_child,
            "running_state_observed_before_kill": observed_running,
            "worker_exit_signal": "SIGKILL",
            "lease_recovery_count": job.worker_recovery_count,
            "old_worker_token_fenced": fenced,
            "replacement_worker_real_ffmpeg_finished": True,
            "output_duration_seconds": float(info["format"]["duration"]),
            "output_sha256": h,
            "limitations": "Tested one actual isolated crash, not distributed multi-node failover.",
        }
        (target/"v15_sigkill_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    main()
