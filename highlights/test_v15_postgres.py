"""V1.5 real PostgreSQL concurrency gates; skipped on SQLite dev CI.

Uses separate Django Client and DB connection in each concurrent thread.
"""
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from highlights.models import Candidate, Export, Game, Venue


class V15ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires actual PostgreSQL row locks")
        self.root = tempfile.mkdtemp(prefix="basketcut-v15-pg-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.scope = override_settings(MEDIA_ROOT=self.root, MEDIA_MIN_FREE_BYTES=0)
        self.scope.enable()
        self.addCleanup(self.scope.disable)
        self.owner = get_user_model().objects.create_user(
            username="v15_shared_owner", password="test-strong-pass")
        self.other = get_user_model().objects.create_user(
            username="v15_other_owner", password="test-strong-pass")
        self.venues = [Venue.objects.create(owner=self.owner, name=f"Court-{n}") for n in range(2)]
        self.other_venue = Venue.objects.create(owner=self.other, name="Other")

    @staticmethod
    def _parallel(callables):
        barrier = threading.Barrier(len(callables))
        def task(fn):
            close_old_connections()
            try:
                return fn(barrier)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(callables)) as pool:
            futures = [pool.submit(task, fn) for fn in callables]
            return [f.result(timeout=50) for f in futures]

    def test_same_owner_concurrent_uploads_cannot_break_quota(self):
        # Each file fits individually; storing both breaks one-owner limit.
        from django.test import Client
        def maker(index):
            def post(barrier):
                client = Client()
                client.force_login(self.owner)
                barrier.wait(timeout=15)
                response = client.post(reverse("upload_game"), {
                    "venue_id": self.venues[index].pk, "title": f"Race-{index}",
                    "video": SimpleUploadedFile(
                        f"race-{index}.mp4", bytes([index + 10]) * 800_000,
                        content_type="video/mp4")})
                return response.status_code
            return post
        with override_settings(OWNER_MAX_RAW_BYTES=1_200_000):
            with patch("highlights.views.probe_duration", return_value=60):
                statuses = self._parallel([maker(0), maker(1)])
        self.assertEqual(statuses, [302, 302])
        self.assertEqual(Game.objects.filter(venue__owner=self.owner).count(), 1)
        self.assertEqual(sum(g.video.size for g in Game.objects.filter(
            venue__owner=self.owner)), 800_000)
        # Different owner's capacity is independent.
        client = __import__("django.test", fromlist=["Client"]).Client()
        client.force_login(self.other)
        with override_settings(OWNER_MAX_RAW_BYTES=1_200_000):
            with patch("highlights.views.probe_duration", return_value=60):
                response = client.post(reverse("upload_game"), {
                    "venue_id": self.other_venue.pk, "title": "Other owner",
                    "video": SimpleUploadedFile("other.mp4", b"X" * 800_000)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Game.objects.filter(venue__owner=self.other).count(), 1)

    def test_concurrent_export_queue_respects_cross_venue_owner_limit(self):
        from django.test import Client
        games = []
        for i, venue in enumerate(self.venues):
            game = Game.objects.create(
                venue=venue, title=f"Export race {i}", duration_seconds=90,
                video=SimpleUploadedFile(f"export-{i}.mp4", b"not-real-video"*600))
            Candidate.objects.create(game=game, start_seconds=2, end_seconds=5,
                                     review="approved", source="manual")
            games.append(game)

        def maker(index):
            def request(barrier):
                client = Client()
                client.force_login(self.owner)
                barrier.wait(timeout=15)
                return client.post(reverse("queue_export", args=[games[index].pk]),
                                   {"player_label": "", "audio_mode": "silent"}).status_code
            return request

        with override_settings(OWNER_MAX_ACTIVE_EXPORTS=1):
            statuses = self._parallel([maker(0), maker(1)])
        self.assertEqual(statuses, [302, 302])
        self.assertEqual(Export.objects.filter(game__venue__owner=self.owner).count(), 1)


class V15UploadBodyGuardTests(TransactionTestCase):
    def test_oversize_header_rejected_before_request_stream_is_parsed(self):
        from django.test import RequestFactory
        from highlights.upload_guard import UploadBodyLimitMiddleware
        request = RequestFactory().post("/games/upload/", data={})
        request.META["CONTENT_LENGTH"] = "20000000"
        with override_settings(MAX_UPLOAD_BYTES=1024):
            response = UploadBodyLimitMiddleware(lambda _: None)(request)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_missing_length_fails_closed(self):
        from django.test import RequestFactory
        from highlights.upload_guard import UploadBodyLimitMiddleware
        request = RequestFactory().post("/games/upload/", data={})
        request.META.pop("CONTENT_LENGTH", None)
        self.assertEqual(UploadBodyLimitMiddleware(lambda _: None)(
            request).status_code, 411)
