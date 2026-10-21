"""V1.4 full-duration CPU sampling coverage, truncation and streaming write tests."""
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files import File
from django.test import TestCase, override_settings

from highlights.models import Game, Venue
from highlights.vision.cpu import scan_cpu_video


class _FakeCapture:
    def __init__(self, seconds, fail_after=None):
        self.seconds = seconds
        self.fail_after = fail_after
        self.seeks = []
        self.current = 0.0

    def isOpened(self):
        return True

    def get(self, prop):
        return 30.0 if prop == 5 else 30.0 * self.seconds

    def set(self, prop, value):
        self.current = value / 1000
        self.seeks.append(self.current)
        return True

    def read(self):
        return (self.fail_after is None or self.current < self.fail_after), object()

    def release(self):
        pass


class V14CoverageTests(TestCase):
    def scan(self, *, seconds, max_frames, fail_after=None):
        cap = _FakeCapture(seconds, fail_after)
        fake_cv2 = types.SimpleNamespace(CAP_PROP_FPS=5, CAP_PROP_FRAME_COUNT=7,
                                        CAP_PROP_POS_MSEC=0,
                                        VideoCapture=lambda _: cap)
        with patch.dict(sys.modules, {"cv2": fake_cv2, "numpy": types.SimpleNamespace()}):
            with patch("highlights.vision.cpu._colored_ball_candidates", return_value=[]):
                result = scan_cpu_video("fake.mp4", max_frames=max_frames)
        return cap, result

    def test_two_hour_video_covers_end_with_frame_cap(self):
        cap, result = self.scan(seconds=7200, max_frames=100)
        detail = result["analysis"]
        self.assertEqual(detail["frames_processed"], 100)
        self.assertTrue(detail["adaptive_sampling"])
        self.assertAlmostEqual(detail["sample_interval_seconds"], 72.0)
        self.assertAlmostEqual(cap.seeks[-1], 7128.0)
        self.assertFalse(detail["incomplete_scan"])
        self.assertEqual(detail["coverage_ratio"], 1.0)
        self.assertEqual(detail["analyzed_until_seconds"], 7200)

    def test_short_video_keeps_original_sampling_interval(self):
        cap, result = self.scan(seconds=14, max_frames=100)
        self.assertFalse(result["analysis"]["adaptive_sampling"])
        self.assertEqual(result["analysis"]["sample_interval_seconds"], .35)
        self.assertEqual(result["analysis"]["coverage_ratio"], 1)

    def test_early_decode_failure_is_explicitly_incomplete(self):
        _, result = self.scan(seconds=3600, max_frames=100, fail_after=1800)
        self.assertTrue(result["analysis"]["incomplete_scan"])
        self.assertLess(result["analysis"]["coverage_ratio"], .55)

    def test_invalid_frame_budget_rejected(self):
        with self.assertRaises(ValueError):
            scan_cpu_video("fake.mp4", max_frames=0)


class _TrackingFile:
    """Real file-backed reader recording largest read() request."""
    def __init__(self, stream):
        self.stream = stream
        self.largest = 0

    def read(self, size=-1):
        if size < 0:
            raise AssertionError("Unbounded read() forbidden")
        self.largest = max(self.largest, size)
        return self.stream.read(size)

    def seek(self, *args):
        return self.stream.seek(*args)

    def tell(self):
        return self.stream.tell()

    def __getattr__(self, key):
        return getattr(self.stream, key)


class V14StreamingStoreTests(TestCase):
    def test_filefield_storage_uses_chunked_reads_not_entire_input(self):
        with tempfile.TemporaryDirectory() as root:
            with override_settings(MEDIA_ROOT=root):
                user = get_user_model().objects.create_user("v14-stream-test")
                venue = Venue.objects.create(owner=user, name="Load Test")
                source = Path(root) / "input.mp4"
                with source.open("wb") as output:
                    for _ in range(16):
                        output.write(os.urandom(64 * 1024))
                with source.open("rb") as fp:
                    reader = _TrackingFile(fp)
                    game = Game.objects.create(
                        venue=venue, title="test", duration_seconds=60,
                        video=File(reader, name="fixture.mp4"))
                self.assertEqual(game.video.size, source.stat().st_size)
                self.assertLessEqual(reader.largest, 1024 * 1024)
                self.assertGreater(reader.largest, 0)


class V14WorkerFailClosedTests(TestCase):
    def test_worker_rejects_truncated_cpu_analysis_without_false_ready(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from highlights.management.commands.runworker import Command

        with tempfile.TemporaryDirectory() as folder:
            with override_settings(MEDIA_ROOT=folder):
                user = get_user_model().objects.create_user("v14-failclosed")
                venue = Venue.objects.create(owner=user, name="Fail-closed court")
                game = Game.objects.create(
                    venue=venue, title="Truncated source",
                    video=SimpleUploadedFile("truncated.mp4", b"some raw bytes"),
                    duration_seconds=7200, state=Game.State.QUEUED,
                    analysis_engine="cpu")
                incomplete = {
                    "analysis": {"incomplete_scan": True, "coverage_ratio": .2},
                    "video_duration_seconds": 7200,
                    "candidates": [{
                        "start_seconds": 2, "end_seconds": 9,
                        "source": "cpu_ball_activity", "score": .7, "note": "unverified"
                    }],
                }
                with patch("highlights.management.commands.runworker.scan_cpu_video",
                           return_value=incomplete):
                    self.assertTrue(Command()._process_one())
                game.refresh_from_db()
                self.assertEqual(game.state, Game.State.FAILED)
                self.assertIn("不完整", game.error)
                self.assertEqual(game.candidates.count(), 0)
