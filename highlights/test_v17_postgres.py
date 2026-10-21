"""Real PostgreSQL lock regression for concurrent resumable reservation creation."""
import json
import tempfile
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from .models import ResumableUpload, Venue


class V17PostgresReservationTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires actual PostgreSQL row locks")
        self.root = tempfile.mkdtemp(prefix="basketcut-v17-pg-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        opts = override_settings(
            MEDIA_ROOT=self.root,
            MEDIA_MIN_FREE_BYTES=0,
            MAX_UPLOAD_BYTES=15_000_000,
            OWNER_MAX_RAW_BYTES=9_000_000,
        )
        opts.enable()
        self.addCleanup(opts.disable)
        self.owner = get_user_model().objects.create_user("v17_pg_owner")
        self.other = get_user_model().objects.create_user("v17_pg_other")
        self.venue = Venue.objects.create(owner=self.owner, name="PG A")
        self.venue_other = Venue.objects.create(owner=self.other, name="PG B")

    def test_concurrent_sessions_cannot_overbook_owner_quota(self):
        gate = threading.Barrier(2)

        def create(index):
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.owner)
                gate.wait(timeout=15)
                response = client.post(
                    reverse("resumable_start"),
                    data=json.dumps({
                        "venue_id": self.venue.pk,
                        "title": "concurrent-" + str(index),
                        "filename": "recording.mp4",
                        "byte_size": 6_000_000,
                        "sha256": "",
                    }),
                    content_type="application/json",
                )
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = [f.result(timeout=35) for f in
                        (pool.submit(create, 0), pool.submit(create, 1))]
        self.assertEqual(sorted(outcomes), [201, 507])
        self.assertEqual(
            ResumableUpload.objects.filter(owner=self.owner, state="receiving").count(), 1)
        other_client = Client()
        other_client.force_login(self.other)
        response = other_client.post(reverse("resumable_start"),
            data=json.dumps({
                "venue_id": self.venue_other.pk,
                "title": "independent budget",
                "filename": "other.mp4",
                "byte_size": 6_000_000,
                "sha256": "",
            }), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ResumableUpload.objects.filter(owner=self.other).count(), 1)
