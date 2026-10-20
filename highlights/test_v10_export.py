"""V1.0 immutable export plan / owner-only download / job recovery regressions."""
import json
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate, Export, Game, Venue
from .export_plan import create_plan, validate_plan, PlanError


class ImmutableReviewedExportTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree,self.tmp,ignore_errors=True)
        setting=override_settings(MEDIA_ROOT=self.tmp)
        setting.enable()
        self.addCleanup(setting.disable)
        U=get_user_model()
        self.owner=U.objects.create_user("v10-owner",password="safe-password")
        self.other=U.objects.create_user("v10-other",password="safe-password")
        self.venue=Venue.objects.create(owner=self.owner,name="Gym")
        self.game=Game.objects.create(
            venue=self.venue,title="Test Basketball",duration_seconds=50,
            video=SimpleUploadedFile("test.webm",b"V10 fixed video identity bytes"*4000))
        self.approved=Candidate.objects.create(
            game=self.game,start_seconds=2,end_seconds=9,
            source="manual",review="approved",player_label="7")
        self.pending=Candidate.objects.create(
            game=self.game,start_seconds=11,end_seconds=15,
            source="cpu_ball_activity",review="pending",player_label="7")
        self.queue=reverse("queue_export",args=[self.game.pk])

    def queue_once(self,label="7"):
        self.client.force_login(self.owner)
        return self.client.post(self.queue,{"player_label":label})

    def test_capture_only_approved_segments_and_dedup_clicks(self):
        self.assertEqual(self.queue_once().status_code,302)
        self.assertEqual(self.queue_once().status_code,302)
        self.assertEqual(Export.objects.filter(game=self.game).count(),1)
        job=Export.objects.get(game=self.game)
        self.assertEqual(job.clips_total,1)
        self.assertEqual(job.attempt,1)
        self.assertEqual(job.plan["clips"][0]["candidate_id"],self.approved.pk)
        self.assertNotIn(self.pending.pk,[i["candidate_id"] for i in job.plan["clips"]])
        self.assertEqual(validate_plan(job),[self.approved])
        self.assertEqual(len(job.plan_sha256),64)

    def test_revoked_review_or_edited_clip_prevents_render(self):
        self.queue_once()
        job=Export.objects.get(game=self.game)
        self.approved.review="rejected"
        self.approved.save(update_fields=["review"])
        with self.assertRaisesRegex(PlanError,"撤销审核"):
            validate_plan(job)
        self.approved.review="approved"
        self.approved.start_seconds=3
        self.approved.save(update_fields=["review","start_seconds"])
        with self.assertRaisesRegex(PlanError,"已被修改"):
            validate_plan(job)

    def test_snapshot_tampering_and_video_replacement_blocked(self):
        self.queue_once()
        job=Export.objects.get(game=self.game)
        job.plan["clips"][0]["end_seconds"]=8
        with self.assertRaisesRegex(PlanError,"校验失败"):
            validate_plan(job)
        job.refresh_from_db()
        path=Path(self.game.video.path)
        path.write_bytes(b"different original media")
        with self.assertRaisesRegex(PlanError,"原始录像已更换"):
            validate_plan(job)

    def test_queue_rejects_no_approvals_or_too_much_content(self):
        self.client.force_login(self.owner)
        self.client.post(self.queue,{"player_label":"nonexistent"})
        self.assertFalse(Export.objects.exists())
        self.approved.end_seconds=160
        self.approved.save(update_fields=["end_seconds"])
        self.client.post(self.queue,{"player_label":"7"})
        self.assertFalse(Export.objects.exists())

    def test_owner_only_manifest_cancel_retry_and_third_try_limit(self):
        self.queue_once()
        job=Export.objects.get(game=self.game)
        manifest=reverse("export_manifest",args=[self.game.pk,job.pk])
        cancel=reverse("cancel_export",args=[self.game.pk,job.pk])
        retry=reverse("retry_export",args=[self.game.pk,job.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(manifest).status_code,404)
        self.assertEqual(self.client.post(cancel).status_code,404)
        self.assertEqual(self.client.post(retry).status_code,404)
        self.client.force_login(self.owner)
        result=self.client.get(manifest)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result["Cache-Control"],"private, no-store")
        data=result.json()
        self.assertEqual(data["plan_sha256"],job.plan_sha256)
        self.assertFalse(data["plan"]["automatic_scoring_claim"])
        self.assertEqual(self.client.post(cancel).status_code,302)
        job.refresh_from_db()
        self.assertEqual(job.state,"cancelled")
        self.client.post(retry)
        self.assertEqual(Export.objects.count(),1)
        # Only FAILED jobs can retry; each retry retains snapshot and original job.
        job.state="failed";job.save(update_fields=["state"])
        self.client.post(retry)
        second=Export.objects.exclude(pk=job.pk).get()
        self.assertEqual(second.retry_of_id,job.pk)
        self.assertEqual(second.attempt,2)
        self.assertEqual(second.plan_sha256,job.plan_sha256)
        self.client.post(retry)
        self.assertEqual(Export.objects.count(),2)
        second.state="failed";second.save(update_fields=["state"])
        self.client.post(reverse("retry_export",args=[self.game.pk,second.pk]))
        third=Export.objects.exclude(pk__in=[job.pk,second.pk]).get()
        self.assertEqual(third.attempt,3)
        third.state="failed";third.save(update_fields=["state"])
        self.client.post(reverse("retry_export",args=[self.game.pk,third.pk]))
        self.assertEqual(Export.objects.count(),3)

    def test_retry_rejects_revoked_approval_without_duplication(self):
        self.queue_once()
        job=Export.objects.get(game=self.game)
        job.state="failed";job.save(update_fields=["state"])
        self.approved.review="rejected";self.approved.save(update_fields=["review"])
        self.client.post(reverse("retry_export",args=[self.game.pk,job.pk]))
        self.assertEqual(Export.objects.count(),1)

    def test_queue_successful_then_requeue_with_new_review_snapshot(self):
        self.queue_once()
        first=Export.objects.get()
        self.approved.end_seconds=10
        self.approved.save(update_fields=["end_seconds"])
        self.queue_once()
        self.assertEqual(Export.objects.count(),2)
        second=Export.objects.exclude(pk=first.pk).get()
        self.assertNotEqual(first.plan_sha256,second.plan_sha256)
        with self.assertRaises(PlanError):
            validate_plan(first)
        self.assertEqual(validate_plan(second),[self.approved])

    def test_cannot_cancel_active_or_completed_export(self):
        self.queue_once()
        job=Export.objects.get()
        cancel=reverse("cancel_export",args=[self.game.pk,job.pk])
        for state in ("running","done","failed"):
            job.state=state;job.save(update_fields=["state"])
            self.client.post(cancel)
            job.refresh_from_db()
            self.assertEqual(job.state,state)

    def test_legacy_export_manifest_explicitly_unversioned(self):
        job=Export.objects.create(game=self.game,player_label="7")
        self.client.force_login(self.owner)
        m=self.client.get(reverse("export_manifest",args=[self.game.pk,job.pk])).json()
        self.assertTrue(m["legacy_export"])
        self.assertIsNone(m["plan"])

    def test_render_matches_approved_snapshot_then_progresses(self):
        from unittest.mock import patch
        from .services import render_export
        self.queue_once()
        job=Export.objects.get()
        # Mock *just FFmpeg*; run all real snapshot validations.
        def fake_command(args):
            # Segment / concat output is always final positional argument.
            Path(args[-1]).write_bytes(b"synthetic mp4 bytes")
        with patch("highlights.services._run_ffmpeg",side_effect=fake_command):
            render_export(job)
        job.refresh_from_db()
        self.assertEqual(job.state,"done")
        self.assertEqual(job.clips_done,1)
        self.assertEqual(job.clips_total,1)
        self.assertGreater(job.video.size,0)

    def test_real_ffmpeg_audio_and_missing_audio_fallback(self):
        import subprocess
        from django.core.files import File
        from .services import render_export, probe_duration, has_audio_stream

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("Requires FFmpeg in CI")
        self.client.force_login(self.owner)
        for with_audio in (True, False):
            with self.subTest(source_has_audio=with_audio):
                name="with-audio.mp4" if with_audio else "no-audio.mp4"
                source=Path(self.tmp)/name
                cmd=["ffmpeg","-nostdin","-loglevel","error","-y",
                     "-f","lavfi","-i","color=c=blue:s=320x240:r=15"]
                if with_audio:
                    cmd+=["-f","lavfi","-i",
                          "sine=frequency=440:sample_rate=48000"]
                cmd+=["-t","2","-c:v","libx264","-pix_fmt","yuv420p"]
                if with_audio:
                    cmd+=["-c:a","aac"]
                cmd+=[str(source)]
                subprocess.run(cmd,capture_output=True,check=True,timeout=90)
                with source.open("rb") as input_video:
                    self.game.video.save(name,File(input_video))
                self.game.duration_seconds=probe_duration(self.game.video.path)
                self.game.save(update_fields=["duration_seconds"])
                self.approved.start_seconds=.2
                self.approved.end_seconds=1.5
                self.approved.save(update_fields=["start_seconds","end_seconds"])
                self.client.post(self.queue,{"player_label":"7","audio_mode":"original"})
                job=Export.objects.filter(game=self.game).latest("pk")
                self.assertEqual(job.audio_mode,"original")
                render_export(job)
                job.refresh_from_db()
                self.assertEqual(job.state,"done")
                self.assertEqual(job.clips_done,1)
                self.assertTrue(has_audio_stream(job.video.path))
