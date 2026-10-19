import os
import subprocess
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate, Export, Game, Venue
from .services import audio_peaks, probe_duration, analyze_game, render_export
from .vision.events import BallObservation, propose_shots, validate_hoops


class VideoFlowTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.tempdir)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        self.owner = get_user_model().objects.create_user("owner", password="pass12345")
        self.other = get_user_model().objects.create_user("other", password="pass12345")
        self.venue = Venue.objects.create(name="Test Venue", owner=self.owner)
        self.game = Game.objects.create(
            venue=self.venue, title="Test 5v5",
            video=SimpleUploadedFile("game.mp4", b"0123456789" * 20),
            duration_seconds=120,
        )

    def test_login_required_and_tenant_separation(self):
        url = reverse("game", args=[self.game.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("source_video", args=[self.game.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("queue_analysis", args=[self.game.pk])).status_code, 404)

    def test_upload_with_probe_and_review_export(self):
        self.client.force_login(self.owner)
        with patch("highlights.views.probe_duration", return_value=90):
            response = self.client.post(reverse("upload_game"), {
                "title": "Sunday pickup", "venue_id": self.venue.pk,
                "video": SimpleUploadedFile("pickup.mp4", b"not-a-real-file", content_type="video/mp4"),
            })
        self.assertEqual(response.status_code, 302)
        game = Game.objects.get(title="Sunday pickup")
        self.assertEqual(game.duration_seconds, 90)
        self.client.post(reverse("add_candidate", args=[game.pk]),
                         {"start": "4", "end": "12", "player_label": "7号"})
        clip = game.candidates.get()
        self.assertEqual(clip.review, "pending")
        response = self.client.post(reverse("review_candidate", args=[game.pk, clip.pk]), {
            "start": "4", "end": "12", "player_label": "7号", "review": "approved"
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(game.candidates.get().review, "approved")
        self.client.post(reverse("queue_export", args=[game.pk]), {"player_label": "7号"})
        self.assertEqual(Export.objects.filter(game=game, state="queued").count(), 1)

    def test_import_gpu_proposals_pending_and_tenant_safe(self):
        import json
        payload = json.dumps({"candidates": [
            {"start_seconds": 10, "end_seconds": 19,
             "source": "visual_ball_presence", "score": .75},
        ]}).encode()
        self.client.force_login(self.other)
        denied = self.client.post(reverse("import_proposals", args=[self.game.pk]),
            {"proposals": SimpleUploadedFile("proposals.json", payload)})
        self.assertEqual(denied.status_code, 404)
        self.client.force_login(self.owner)
        url = reverse("import_proposals", args=[self.game.pk])
        for _ in range(2):
            self.assertEqual(self.client.post(url, {"proposals":
                SimpleUploadedFile("proposals.json", payload)}).status_code, 302)
        self.assertEqual(self.game.candidates.count(), 1)
        self.assertEqual(self.game.candidates.first().review, "pending")

    def test_game_detail_renders_roi_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("game", args=[self.game.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "roi-layer")
        self.assertContains(response, "vision-config")
        self.assertContains(response, "hoop-roi.")

    def test_hoop_settings_tenant_and_config(self):
        import json
        self.client.force_login(self.other)
        url = reverse("save_hoops", args=[self.game.pk])
        self.assertEqual(self.client.post(url, {"hoops": "[]"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("vision_config", args=[self.game.pk])).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("vision_config", args=[self.game.pk])).status_code, 400)
        hoops = [{"x": .4, "y": .1, "w": .1, "h": .1}]
        self.assertEqual(self.client.post(url, {"hoops": json.dumps(hoops)}).status_code, 302)
        self.game.refresh_from_db()
        self.assertEqual(self.game.hoops, hoops)
        config = self.client.get(reverse("vision_config", args=[self.game.pk]))
        self.assertEqual(config.status_code, 200)
        self.assertEqual(config.json()["game_id"], self.game.pk)
        self.assertEqual(config.json()["hoops"], hoops)
        self.client.post(url, {"hoops": '[{"x":NaN,"y":0.1,"w":0.1,"h":0.1}]'})
        self.game.refresh_from_db()
        self.assertEqual(self.game.hoops, hoops)

    def test_reject_wrong_video_metadata(self):
        import json
        self.client.force_login(self.owner)
        url = reverse("import_proposals", args=[self.game.pk])
        proposal = {"schema_version": 2, "game_id": self.game.pk + 1,
                    "video_duration_seconds": 120,
                    "candidates": [{"start_seconds": 5, "end_seconds": 12,
                                    "source": "model_proposal", "score": .8}]}
        response = self.client.post(url, {"proposals":
            SimpleUploadedFile("clip.json", json.dumps(proposal).encode())})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.game.candidates.exists())
        proposal["game_id"] = self.game.pk
        proposal["video_duration_seconds"] = 150
        self.client.post(url, {"proposals":
            SimpleUploadedFile("clip.json", json.dumps(proposal).encode())})
        self.assertFalse(self.game.candidates.exists())
        proposal["video_duration_seconds"] = 120
        self.client.post(url, {"proposals":
            SimpleUploadedFile("clip.json", json.dumps(proposal).encode())})
        self.assertEqual(self.game.candidates.count(), 1)
        self.assertEqual(self.game.candidates.first().review, "pending")

    def test_bad_clip_times_not_created(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("add_candidate", args=[self.game.pk]),
                                    {"start": "50", "end": "200"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.game.candidates.exists())

    def test_private_range_video(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("source_video", args=[self.game.pk]),
                                   HTTP_RANGE="bytes=10-19")
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response["Content-Range"], "bytes 10-19/200")
        self.assertEqual(b"".join(response.streaming_content), b"0123456789")

    def test_delete_removes_local_original(self):
        self.client.force_login(self.owner)
        path = self.game.video.path
        self.assertTrue(Path(path).exists())
        response = self.client.post(reverse("delete_game", args=[self.game.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Path(path).exists())

    def test_worker_marks_analysis_ready(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("queue_analysis", args=[self.game.pk]))
        with patch("highlights.management.commands.runworker.analyze_game") as analyze:
            from highlights.management.commands.runworker import Command
            analyze.side_effect = lambda game: Game.objects.filter(pk=game.pk).update(state="ready") or 1
            self.assertTrue(Command()._process_one())
        self.game.refresh_from_db()
        self.assertEqual(self.game.state, "ready")

    def test_real_ffmpeg_end_to_end(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg not installed")
        source = Path(self.tempdir) / "fixture.mp4"
        subprocess.run([
            "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=10",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000",
            "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ], capture_output=True, check=True, timeout=90)
        with source.open("rb") as stream:
            self.game.video.save("fixture.mp4", File(stream))
        self.game.duration_seconds = probe_duration(self.game.video.path)
        self.game.save(update_fields=["duration_seconds"])
        analyze_game(self.game)
        self.game.refresh_from_db()
        self.assertEqual(self.game.state, "ready")
        Candidate.objects.create(
            game=self.game, start_seconds=.5, end_seconds=2.5,
            review="approved", player_label="7号", source="manual")
        job = Export.objects.create(game=self.game, player_label="7号")
        render_export(job)
        job.refresh_from_db()
        self.assertEqual(job.state, "done")
        self.assertTrue(Path(job.video.path).exists())
        self.assertGreater(Path(job.video.path).stat().st_size, 500)
        self.assertGreater(probe_duration(job.video.path), 1)

    def test_no_unauthorized_export(self):
        clip = Candidate.objects.create(game=self.game, start_seconds=1, end_seconds=4, review="approved")
        export = Export.objects.create(game=self.game, state="done",
                    video=SimpleUploadedFile("out.mp4", b"abcd"))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("export_video", args=[self.game.pk, export.pk])).status_code, 404)


class AudioUnitTests(TestCase):
    def test_probe_invalid(self):
        with self.assertRaises(Exception):
            probe_duration("/no-such-video.mp4")

    def test_audio_no_stream(self):
        # Test missing media results in no fake detected basket.
        result = audio_peaks("/no-such-file.mp4", 60)
        self.assertEqual(result, [])


class VisionEventTests(TestCase):
    def test_near_hoop_event_not_a_scored_basket(self):
        hoops = [{"x": .4, "y": .2, "w": .1, "h": .06}]
        points = [BallObservation(t, x, .25, .83)
                  for t, x in ((10, .45), (11.5, .47), (30, .1), (31.5, .9))]
        clips = propose_shots(points, hoops, duration=60)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["source"], "model_proposal")
        self.assertIn("非得分结论", clips[0]["note"])
        self.assertEqual(clips[0]["start_seconds"], 5)
        self.assertEqual(clips[0]["end_seconds"], 18.5)

    def test_no_frames_or_invalid_region(self):
        hoops = [{"x": .4, "y": .2, "w": .1, "h": .06}]
        self.assertEqual(propose_shots([], hoops, 60), [])
        self.assertEqual(propose_shots([BallObservation(5, .45, .24, .9)], hoops, 60), [])
        with self.assertRaises(ValueError):
            validate_hoops([{"x": .9, "y": .4, "w": .3, "h": .1}])
        with self.assertRaises(ValueError):
            validate_hoops([{"x": float("nan"), "y": .4, "w": .1, "h": .1}])

    def test_far_from_hoop_false_ball_detection_is_not_a_shot(self):
        """Observed CPU pre-trained box in the bottom court is not a rim event."""
        hoops = [{"x": .24, "y": .37, "w": .08, "h": .1}]
        false_boxes = [
            BallObservation(2, .49, .817, .54),
            BallObservation(2.8, .542, .818, .37),
        ]
        self.assertEqual(propose_shots(false_boxes, hoops, 5.767), [])

    def test_deduplicate_overlapping_two_hoops(self):
        hoops = [{"x": .4, "y": .2, "w": .1, "h": .06},
                 {"x": .42, "y": .2, "w": .1, "h": .06}]
        points = [BallObservation(t, .45, .24, .9) for t in (4, 5, 6)]
        self.assertEqual(len(propose_shots(points, hoops, 40)), 1)
