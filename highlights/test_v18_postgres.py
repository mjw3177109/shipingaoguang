"""PostgreSQL cross-tenant global physical admission serialization.

Uses real PG SELECT FOR UPDATE with separate Django DB connections.
Free disk is *injected* to simulate a capacity limit; no physical space is
actually reserved. Demonstrates admission serialization only.
"""
import json
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, connections, close_old_connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from highlights.models import MediaCapacityLock, ResumableUpload, Venue


class V18CrossTenantPostgresTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires actual PostgreSQL, not SQLite")
        MediaCapacityLock.objects.get_or_create(pk=1)
        self.root = tempfile.mkdtemp(prefix="basketcut-v18-pg-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        scope = override_settings(
            MEDIA_ROOT=self.root, MEDIA_MIN_FREE_BYTES=100,
            OWNER_MAX_RAW_BYTES=10000, MAX_UPLOAD_BYTES=10000)
        scope.enable()
        self.addCleanup(scope.disable)
        self.users = [
            get_user_model().objects.create_user("v18_cross_owner_a"),
            get_user_model().objects.create_user("v18_cross_owner_b"),
        ]
        self.venues = [
            Venue.objects.create(owner=self.users[i], name=f"Court {i}")
            for i in range(2)
        ]
        self.assertTrue(MediaCapacityLock.objects.filter(pk=1).exists())

    def test_different_owners_share_global_disk_lease_without_overadmission(self):
        gate = threading.Barrier(2)

        def create(index):
            close_old_connections()
            try:
                client = Client()
                client.force_login(self.users[index])
                gate.wait(timeout=15)
                response = client.post(
                    reverse("resumable_start"),
                    data=json.dumps({
                        "venue_id": self.venues[index].pk,
                        "title": f"cross-tenant-{index}",
                        "filename": "match.mp4", "byte_size": 600,
                        "sha256": "",
                    }), content_type="application/json")
                return response.status_code
            finally:
                connections.close_all()

        with patch("highlights.operations.media_disk_free_bytes", return_value=1000):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [task.result(timeout=35) for task in
                           (pool.submit(create, 0), pool.submit(create, 1))]
        self.assertEqual(sorted(results), [201, 507])
        self.assertEqual(ResumableUpload.objects.count(), 1)
        self.assertEqual(sum(x.byte_size-x.received_bytes
                         for x in ResumableUpload.objects.all()), 600)
