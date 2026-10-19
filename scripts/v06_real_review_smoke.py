#!/usr/bin/env python3
"""Real licensed footage + SYNTHETIC temporal observations end-to-end bridge smoke.

This is NOT an actual basketball detection demonstration. Fake observations only
exercise guarded importer/review/export plumbing with a real, decodable video.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE","basketcut.settings")
import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from highlights.models import Candidate, Game, Export, Venue
from highlights.services import probe_duration
from highlights.management.commands.runworker import Command
from highlights.vision.bridge_v06 import media_quick_fingerprint


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--video",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    video=Path(a.video).resolve()
    target=Path(a.out).resolve();target.mkdir(parents=True,exist_ok=True)
    users=get_user_model()
    user=users.objects.create_user(
        username="v06_research_smoke",password="v06-temporary-ci-only")
    venue=Venue.objects.create(name="V06 Real-Footage Technical Smoke",owner=user)
    web=Client(HTTP_HOST="localhost");web.force_login(user)
    with video.open("rb") as stream:
        response=web.post(reverse("upload_game"),{
            "title":"V0.6 CC0 Integration Test",
            "venue_id":venue.pk,
            "video":SimpleUploadedFile(video.name,stream.read(),
                    content_type="video/webm")})
    if response.status_code!=302:
        raise RuntimeError("Real footage Django upload failure")
    game=Game.objects.get(venue=venue)
    hoops=[{"x":.4,"y":.18,"w":.1,"h":.1}]
    response=web.post(reverse("save_hoops",args=[game.pk]),{
        "hoops":json.dumps(hoops)})
    if response.status_code!=302:
        raise RuntimeError("Cannot set hoop ROI")
    game.refresh_from_db()
    duration=probe_duration(game.video.path)
    observations=[{"time":1.2,"x":.42,"y":.23,"confidence":.84},
                  {"time":1.9,"x":.445,"y":.21,"confidence":.88},
                  {"time":2.6,"x":.47,"y":.18,"confidence":.91}]
    # IMPORTANT: synthetic observations; deliberately NOT claimed to originate
    # from actual neural-network inference on the real source video.
    payload={
        "schema_version":2,"game_id":game.pk,"video_duration_seconds":duration,
        "video_quick_fingerprint_sha256":media_quick_fingerprint(game.video.path),
        "hoops":hoops,"review_only":True,"research_only":True,
        "observations_truncated":False,
        "analysis":{"device":"cpu","checkpoint_used":True,
            "model":"basketball_specialist_mobile320_v1","incomplete_scan":False,
            "analyzed_until_seconds":duration},
        "observations":observations,
    }
    response=web.post(reverse("import_research_observations",args=[game.pk]),{
        "observations":SimpleUploadedFile("SYNTHETIC-bridge-fixture.json",
                      json.dumps(payload).encode(),content_type="application/json")})
    if response.status_code!=302:
        raise RuntimeError("V06 research importer failure")
    candidates=list(Candidate.objects.filter(game=game,source="model_proposal"))
    if len(candidates)!=1 or candidates[0].review!="pending":
        raise RuntimeError("Research importer did not produce exactly one PENDING candidate")
    if game.exports.exists():
        raise RuntimeError("Unreviewed clips must not auto-export")
    clip=candidates[0]
    response=web.post(reverse("review_candidate",args=[game.pk,clip.pk]),{
        "review":"approved","start":clip.start_seconds,"end":clip.end_seconds,
        "note":"CI SIMULATED reviewer, not a real validated shot"})
    if response.status_code!=302 or game.candidates.get(pk=clip.pk).review!="approved":
        raise RuntimeError("Review endpoint failure")
    web.post(reverse("queue_export",args=[game.pk]),{"player_label":""})
    if not Command()._process_one():
        raise RuntimeError("Render worker found no job")
    job=Export.objects.get(game=game)
    if job.state!="done" or not job.video:
        raise RuntimeError("FFmpeg export failed "+job.error)
    file=target/"integration_highlight.mp4"
    shutil.copyfile(job.video.path,file)
    report={
        "original_source":"https://commons.wikimedia.org/wiki/File:Basketpass.webm",
        "original_license":"CC0 1.0",
        "original_video_duration":round(duration,3),
        "video_is_real":True,
        "observations_are_synthetic":True,
        "specialist_neural_inference_executed":False,
        "manual_review_in_CI":"SIMULATED",
        "v06_bridge_candidates":len(candidates),
        "candidate_review_status_before_test_review":"pending",
        "export_created_only_after_test_review":True,
        "video_rendered":True,
        "rendered_video_duration":round(probe_duration(file),3),
        "rendered_size_bytes":file.stat().st_size,
        "rendered_sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
        "accuracy":"NOT MEASURED. Synthetic observations cannot prove basketball recognition.",
    }
    (target/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
