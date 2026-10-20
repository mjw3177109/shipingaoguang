#!/usr/bin/env python3
"""Run actual basketball footage through Django upload -> CPU worker -> review -> FFmpeg.

A simulated reviewer approves a candidate (or uses manual fallback). This is an
integration smoke test, NOT proof that a shot has been detected correctly.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "basketcut.settings")
import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from highlights.management.commands.runworker import Command
from highlights.models import Candidate, Export, Game, Venue
from highlights.services import probe_duration, has_audio_stream


def _hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--out", default="real-video-results")
    p.add_argument("--source-url", default="")
    p.add_argument("--license", default="")
    p.add_argument("--title", default="Creative Commons Basketball E2E")
    args = p.parse_args()
    src = Path(args.video).resolve()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise FileNotFoundError(src)
    started = time.monotonic()

    # Django URLs, permissions and actual uploaded FileField are exercised.
    user = get_user_model().objects.create_user(
        username="smoke_test_operator", password="smoke-test-local-only")
    venue = Venue.objects.create(owner=user, name="Authorized Test Court")
    web = Client(HTTP_HOST="localhost")
    web.force_login(user)
    with src.open("rb") as file:
        uploaded = SimpleUploadedFile(src.name, file.read(), content_type="video/webm"
                                      if src.suffix == ".webm" else "video/mp4")
    response = web.post(reverse("upload_game"), {"title": args.title,
                         "venue_id": venue.pk, "video": uploaded})
    if response.status_code != 302:
        raise RuntimeError("Upload endpoint status: " + str(response.status_code))
    game = Game.objects.get(venue=venue, title=args.title)
    video_duration = probe_duration(game.video.path)
    response = web.post(reverse("queue_analysis", args=[game.pk]), {"engine": "cpu"})
    if response.status_code != 302:
        raise RuntimeError("CPU queue endpoint status: " + str(response.status_code))
    if game.pk != Game.objects.get(pk=game.pk).pk:
        raise RuntimeError("Game disappeared")
    worker = Command()
    if not worker._process_one():
        raise RuntimeError("CPU worker found no task")
    game.refresh_from_db()
    if game.state != Game.State.READY:
        raise RuntimeError("CPU analysis failure: " + game.error)
    cpu_candidates = list(game.candidates.filter(
        source__in=["cpu_ball_activity", "cpu_hoop_activity"]))
    cpu_count = len(cpu_candidates)
    manual_fallback = cpu_count == 0
    if manual_fallback:
        selected = Candidate.objects.create(
            game=game, start_seconds=.25,
            end_seconds=min(video_duration, 5),
            source="manual", note="E2E manual fallback - NOT AI detected")
    else:
        selected = cpu_candidates[0]

    response = web.post(reverse("review_candidate", args=[game.pk, selected.pk]), {
        "start": selected.start_seconds, "end": selected.end_seconds,
        "review": "approved", "player_label": "", "note": selected.note,
    })
    if response.status_code != 302 or game.candidates.get(pk=selected.pk).review != "approved":
        raise RuntimeError("Review endpoint failed")
    response = web.post(reverse("queue_export", args=[game.pk]), {"player_label": ""})
    if response.status_code != 302:
        raise RuntimeError("Export queue endpoint failed")
    job = Export.objects.filter(game=game).latest("pk")
    if not job.plan or job.plan.get("clip_count") != 1 or len(job.plan_sha256) != 64:
        raise RuntimeError("V1.0 must freeze exactly one approved clip before rendering")
    manifest_response = web.get(reverse("export_manifest", args=[game.pk, job.pk]))
    if manifest_response.status_code != 200:
        raise RuntimeError("Owner-only edit decision list download failed")
    edit_list = manifest_response.json()
    if edit_list["plan_sha256"] != job.plan_sha256 or edit_list["plan"]["clips"][0]["candidate_id"] != selected.pk:
        raise RuntimeError("V1.0 immutable snapshot mismatch")
    # Double click is harmless: no duplicate queued export for same selection.
    web.post(reverse("queue_export", args=[game.pk]), {"player_label": ""})
    if Export.objects.filter(game=game).count() != 1:
        raise RuntimeError("V1.0 duplicate click enqueued multiple exports")
    if not worker._process_one():
        raise RuntimeError("Export worker found no task")
    job.refresh_from_db()
    if job.state != Export.State.DONE or not job.video:
        raise RuntimeError("FFmpeg export failure: " + job.error)
    highlight = output / "highlight.mp4"
    shutil.copyfile(job.video.path, highlight)
    final_duration = probe_duration(highlight)
    if not has_audio_stream(highlight):
        raise RuntimeError("V1.0 requested original audio but final MP4 has no audio track")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-y", "-ss", str(min(.5, final_duration/2)), "-i", str(highlight),
                    "-frames:v", "1", str(output/"preview.jpg")], timeout=80, check=True)
    if job.clips_done != 1 or job.clips_total != 1:
        raise RuntimeError("V1.0 rendered clip progress did not complete")
    (output/"edit_list.json").write_text(
        json.dumps(edit_list,ensure_ascii=False,indent=2),encoding="utf-8")
    report = {
        "output_has_audio": True,
        "output_audio_mode": job.audio_mode,
        "immutable_export_plan_verified": True,
        "export_plan_sha256": job.plan_sha256,
        "queued_duplicate_suppressed": True,
        "clips_completed": job.clips_done,
        "export_human_review_in_test": "SIMULATED, not a real expert",
        "source": args.source_url, "license": args.license,
        "source_sha256": _hash(src), "source_bytes": src.stat().st_size,
        "original_duration_seconds": round(video_duration, 3),
        "cpu_candidate_count": cpu_count,
        "cpu_candidates": [{"start": c.start_seconds, "end": c.end_seconds,
                            "score": c.energy_score, "source": c.source}
                           for c in cpu_candidates],
        "candidate_selected": {"source": selected.source, "start": selected.start_seconds,
                               "end": selected.end_seconds},
        "manual_fallback": manual_fallback,
        "review_in_test": "automated test approval, NOT human-validated shot label",
        "export_success": True,
        "output_bytes": highlight.stat().st_size,
        "output_seconds": round(final_duration, 3),
        "output_sha256": _hash(highlight),
        "overall_elapsed_seconds": round(time.monotonic()-started, 2),
        "accuracy": "NOT MEASURED: no independent complete shot_attempt labels",
        "disclaimer": "CPU heuristic proposals are NOT automatic made-shot classifications.",
    }
    (output/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
