"""CPU baseline regression tests; synthetic frame fixture is NOT accuracy evidence."""
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Candidate, Game, Venue
from .vision.cpu import scan_cpu_video


class CPUBallDetectorUnitTests(TestCase):
    def test_moving_orange_circle_produces_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "orange_ball.avi")
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(path, fourcc, 8, (320, 240))
            if not writer.isOpened():
                self.skipTest("MJPG encoder unavailable on CI host")
            try:
                for i in range(24):
                    frame = np.zeros((240, 320, 3), dtype=np.uint8)
                    frame[:] = (40, 60, 55)
                    cv2.circle(frame, (35 + 7*i, 90), 10, (0, 130, 255), -1)
                    writer.write(frame)
            finally:
                writer.release()
            result = scan_cpu_video(path, sample_seconds=.25)
            self.assertGreater(result["analysis"]["frames_processed"], 0)
            self.assertGreater(len(result["candidates"]), 0)
            self.assertTrue(all(x["source"] == "cpu_ball_activity"
                                for x in result["candidates"]))
            self.assertEqual(result["analysis"]["device"], "cpu")
            self.assertFalse(result["analysis"]["incomplete_scan"])

    def test_blank_video_does_not_invent_a_basket(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "empty.avi")
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 8, (320,240))
            if not writer.isOpened():
                self.skipTest("MJPG encoder unavailable")
            for _ in range(16):
                writer.write(np.zeros((240,320,3), dtype=np.uint8))
            writer.release()
            result = scan_cpu_video(path)
            self.assertEqual(result["candidates"], [])

    def test_bad_input_fails_clearly(self):
        with self.assertRaises(ValueError):
            scan_cpu_video("/not-found.webm")


class CPUWebIntegrationTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.tempdir)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        self.owner = get_user_model().objects.create_user("cpu-owner", password="12345Secure")
        self.other = get_user_model().objects.create_user("cpu-other", password="12345Secure")
        self.venue = Venue.objects.create(owner=self.owner, name="CPU Arena")
        self.game = Game.objects.create(
            venue=self.venue, title="CPU Game",
            video=SimpleUploadedFile("game.mp4", b"fixture"),
            duration_seconds=60,
        )

    def test_cpu_analysis_queue_and_worker(self):
        url = reverse("queue_analysis", args=[self.game.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"engine": "cpu"}).status_code, 404)
        self.client.force_login(self.owner)
        response = self.client.post(url, {"engine": "cpu"})
        self.assertEqual(response.status_code, 302)
        self.game.refresh_from_db()
        self.assertEqual(self.game.analysis_engine, "cpu")
        self.assertEqual(self.game.state, "queued")
        generated = {
            "video_duration_seconds": 60, "candidates": [{
                "source": "cpu_ball_activity", "start_seconds": 10,
                "end_seconds": 17, "score": .5, "note": "CPU-only heuristic",
            }],
            "analysis": {"frames_processed": 40},
        }
        with patch("highlights.management.commands.runworker.scan_cpu_video",
                   return_value=generated):
            from .management.commands.runworker import Command
            self.assertTrue(Command()._process_one())
        self.game.refresh_from_db()
        self.assertEqual(self.game.state, "ready")
        candidate = Candidate.objects.get(game=self.game, source="cpu_ball_activity")
        self.assertEqual(candidate.review, "pending")

    def test_engine_input_validation(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("queue_analysis", args=[self.game.pk]),
                         {"engine": "not-allowed"})
        self.game.refresh_from_db()
        self.assertEqual(self.game.state, "uploaded")
        self.assertEqual(self.game.analysis_engine, "audio")
