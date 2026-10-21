"""V1.8 global staging admission regression; disk reservations are *logical*."""
import json
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from highlights.models import MediaCapacityLock, ResumableUpload, Venue
from highlights.operations import (
    InsufficientStorage, lock_media_admission,
    pending_staging_bytes, require_media_admission,
)


class V18MediaAdmissionTests(TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="basketcut-v18-media-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        limit = override_settings(
            MEDIA_ROOT=self.temp, MEDIA_MIN_FREE_BYTES=100,
            OWNER_MAX_RAW_BYTES=10000, MAX_UPLOAD_BYTES=10000)
        limit.enable()
        self.addCleanup(limit.disable)
        self.one = get_user_model().objects.create_user("v18_tenant_a")
        self.two = get_user_model().objects.create_user("v18_tenant_b")
        self.venue_a = Venue.objects.create(owner=self.one, name="A")
        self.venue_b = Venue.objects.create(owner=self.two, name="B")
        self.assertTrue(MediaCapacityLock.objects.filter(pk=1).exists())

    def start(self, owner, venue, size):
        self.client.force_login(owner)
        return self.client.post(reverse("resumable_start"),
            data=json.dumps({"venue_id": venue.pk, "title": "V18",
                             "filename": "video.mp4", "sha256": "", "byte_size": size}),
            content_type="application/json")

    def test_cross_owner_admission_blocked_by_pending_unwritten_capacity(self):
        with patch("highlights.operations.media_disk_free_bytes", return_value=1000):
            first = self.start(self.one, self.venue_a, 600)
            self.assertEqual(first.status_code, 201, first.content)
            self.assertEqual(pending_staging_bytes(), 600)
            second = self.start(self.two, self.venue_b, 400)
            self.assertEqual(second.status_code, 507)
            self.assertEqual(ResumableUpload.objects.count(), 1)
            self.assertEqual(pending_staging_bytes(), 600)

    def test_legacy_upload_respects_other_tenants_unfinished_reservation(self):
        with patch("highlights.operations.media_disk_free_bytes", return_value=1000):
            first = self.start(self.one, self.venue_a, 600)
            self.assertEqual(first.status_code, 201)
            self.client.force_login(self.two)
            response = self.client.post(reverse("upload_game"), {
                "venue_id": self.venue_b.pk, "title": "old",
                "video": SimpleUploadedFile("old.mp4", b"Q"*400)})
        self.assertEqual(response.status_code, 302)
        from highlights.models import Game
        self.assertEqual(Game.objects.count(), 0)

    def test_cancel_releases_global_pending_lease(self):
        with patch("highlights.operations.media_disk_free_bytes", return_value=1000):
            first = self.start(self.one, self.venue_a, 600)
            self.assertEqual(self.start(self.two, self.venue_b, 400).status_code, 507)
            self.client.force_login(self.one)
            stopped = self.client.delete(
                reverse("resumable_status", args=[first.json()["id"]]))
            self.assertEqual(stopped.status_code, 204)
            self.assertEqual(pending_staging_bytes(), 0)
            self.assertEqual(self.start(self.two, self.venue_b, 400).status_code, 201)

    def test_partial_upload_changes_remaining_promise(self):
        session_result = self.start(self.one, self.venue_a, 600)
        self.assertEqual(session_result.status_code, 201)
        session = ResumableUpload.objects.get(pk=session_result.json()["id"])
        session.received_bytes = 275
        session.save(update_fields=["received_bytes"])
        self.assertEqual(pending_staging_bytes(), 325)
        with patch("highlights.operations.media_disk_free_bytes", return_value=800):
            with transaction.atomic():
                lock_media_admission()
                result = require_media_admission(375)
                self.assertEqual(result["promised_unwritten_bytes"], 325)
                self.assertEqual(result["additional_bytes"], 375)

    def test_expired_upload_no_longer_holds_unwritten_lease(self):
        self.assertEqual(self.start(self.one, self.venue_a, 600).status_code, 201)
        ResumableUpload.objects.update(expires_at=timezone.now()-__import__(
            "datetime").timedelta(days=1))
        self.assertEqual(pending_staging_bytes(), 0)

    def test_low_disk_budget_during_chunk_does_not_advance_offset(self):
        first = self.start(self.one, self.venue_a, 600)
        self.assertEqual(first.status_code, 201)
        import hashlib
        data = b"x"*200
        self.client.force_login(self.one)
        with patch("highlights.operations.media_disk_free_bytes", return_value=500):
            response = self.client.post(
                reverse("resumable_chunk", args=[first.json()["id"]]),
                data=data, content_type="application/octet-stream",
                HTTP_X_UPLOAD_OFFSET="0",
                HTTP_X_CHUNK_SHA256=hashlib.sha256(data).hexdigest())
        self.assertEqual(response.status_code, 507)
        self.assertEqual(ResumableUpload.objects.get(
            pk=first.json()["id"]).received_bytes, 0)

    def test_lock_does_not_silently_recreate_missing_singleton(self):
        MediaCapacityLock.objects.filter(pk=1).delete()
        with self.assertRaises(MediaCapacityLock.DoesNotExist):
            with transaction.atomic():
                lock_media_admission()

    def test_bad_amount_and_reserve_rejected(self):
        with self.assertRaises(ValueError):
            require_media_admission(-1)
        with self.assertRaises(ValueError):
            require_media_admission(0, reserve_bytes=-1)
