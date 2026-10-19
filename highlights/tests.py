import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate, Export, Game, Venue
from .services import audio_peaks, probe_duration


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
