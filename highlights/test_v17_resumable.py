"""V1.7 regression: small-file integration for resumable upload protocol.

Django client tests HTTP routing, authentication, SHA, offset, quota, cleanup.
No real multi-GiB performance assertion is made here.
"""
import hashlib
import json
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Game, ResumableUpload, Venue
from .resumable import session_path


class V17ResumableTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="basketcut-v17-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        scope = override_settings(MEDIA_ROOT=self.tmp, MEDIA_MIN_FREE_BYTES=0,
                                  MAX_UPLOAD_BYTES=10*1024*1024,
                                  OWNER_MAX_RAW_BYTES=128*1024*1024)
        scope.enable()
        self.addCleanup(scope.disable)
        self.owner = get_user_model().objects.create_user("v17_owner", password="safe!")
        self.other = get_user_model().objects.create_user("v17_other", password="safe!")
        self.venue = Venue.objects.create(owner=self.owner, name="Resume court")
        self.client.force_login(self.owner)
        self.raw = b"example media content-" * 1000

    def start(self, *, size=None, sha=None, filename="game.mp4"):
        payload = {
            "venue_id": self.venue.pk, "title": "V17 Test",
            "filename": filename,
            "byte_size": size if size is not None else len(self.raw),
            "sha256": self.hash(self.raw) if sha is None else sha,
        }
        return self.client.post(reverse("resumable_start"),
                                data=json.dumps(payload),
                                content_type="application/json")

    @staticmethod
    def hash(data):
        return hashlib.sha256(data).hexdigest()

    def chunk(self, upload_id, start, data, *, digest=None):
        return self.client.post(reverse("resumable_chunk", args=[upload_id]),
            data=data, content_type="application/octet-stream",
            HTTP_X_UPLOAD_OFFSET=str(start),
            HTTP_X_CHUNK_SHA256=digest or self.hash(data))

    def test_two_chunks_complete_idempotent_and_original_file_sha(self):
        r = self.start()
        self.assertEqual(r.status_code, 201, r.content)
        uid = r.json()["id"]
        m = len(self.raw) // 2
        first = self.chunk(uid, 0, self.raw[:m])
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["offset"], m)
        status = self.client.get(reverse("resumable_status", args=[uid]))
        self.assertEqual(status.json()["offset"], m)
        self.assertEqual(self.chunk(uid, 0, self.raw[:m]).status_code, 409)
        self.assertEqual(self.chunk(uid, m, self.raw[m:]).status_code, 200)
        with patch("highlights.resumable_views.probe_duration", return_value=3600):
            final = self.client.post(reverse("resumable_finish", args=[uid]))
        self.assertEqual(final.status_code, 200, final.content)
        self.assertEqual(final.json()["sha256"], self.hash(self.raw))
        game = Game.objects.get(pk=final.json()["game_id"])
        self.assertEqual(Path(game.video.path).read_bytes(), self.raw)
        self.assertFalse(session_path(ResumableUpload.objects.get(pk=uid)).exists())
        second = self.client.post(reverse("resumable_finish", args=[uid]))
        self.assertEqual(second.json()["game_id"], game.pk)
        self.assertEqual(Game.objects.count(), 1)

    def test_unauthorized_cross_tenant_chunk_status_and_cancel(self):
        uid = self.start().json()["id"]
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("resumable_status", args=[uid])).status_code, 404)
        self.assertEqual(self.chunk(uid, 0, b"part").status_code, 404)
        self.assertEqual(self.client.delete(reverse("resumable_status", args=[uid])).status_code, 404)

    def test_chunk_hash_mismatch_does_not_commit_progress_and_retry_recovers(self):
        uid = self.start().json()["id"]
        response = self.chunk(uid, 0, self.raw[:250], digest="0"*64)
        self.assertEqual(response.status_code, 422, response.content)
        session = ResumableUpload.objects.get(pk=uid)
        self.assertEqual(session.received_bytes, 0)
        self.assertEqual(self.chunk(uid, 0, self.raw).status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.received_bytes, len(self.raw))
        self.assertEqual(session_path(session).stat().st_size, len(self.raw))

    def test_crashed_uncommitted_tail_is_truncated_to_db_offset(self):
        uid = self.start().json()["id"]
        self.assertEqual(self.chunk(uid, 0, self.raw[:100]).status_code, 200)
        session = ResumableUpload.objects.get(pk=uid)
        with session_path(session).open("ab") as fp:
            fp.write(b"uncommitted")
        self.assertEqual(self.chunk(uid, 100, self.raw[100:]).status_code, 200)
        self.assertEqual(session_path(session).read_bytes(), self.raw)

    def test_partial_file_missing_fail_closed(self):
        uid = self.start().json()["id"]
        self.assertEqual(self.chunk(uid, 0, self.raw[:100]).status_code, 200)
        session = ResumableUpload.objects.get(pk=uid)
        session_path(session).unlink()
        self.assertEqual(self.chunk(uid, 100, self.raw[100:]).status_code, 507)
        session.refresh_from_db()
        self.assertEqual(session.received_bytes, 100)

    def test_incomplete_rejected_cancel_deletes_staging(self):
        uid = self.start().json()["id"]
        self.assertEqual(self.chunk(uid, 0, self.raw[:100]).status_code, 200)
        self.assertEqual(self.client.post(reverse("resumable_finish", args=[uid])).status_code, 409)
        session = ResumableUpload.objects.get(pk=uid)
        self.assertTrue(session_path(session).exists())
        self.assertEqual(self.client.delete(reverse("resumable_status", args=[uid])).status_code, 204)
        self.assertFalse(session_path(session).exists())
        self.assertEqual(self.chunk(uid, 100, self.raw[100:]).status_code, 410)

    def test_owner_quota_reserves_all_incomplete_sessions(self):
        with override_settings(OWNER_MAX_RAW_BYTES=len(self.raw)*2-1):
            self.assertEqual(self.start().status_code, 201)
            self.assertEqual(self.start().status_code, 507)
        with override_settings(OWNER_MAX_RAW_BYTES=len(self.raw)+10):
            original = self.client.post(reverse("upload_game"), {
                "venue_id": self.venue.pk, "title": "Old route",
                "video": SimpleUploadedFile("old.mp4", b"validbytes"*10)})
            self.assertEqual(original.status_code, 302)
            self.assertEqual(Game.objects.count(), 0)

    def test_expired_session_refuses_chunks(self):
        uid = self.start().json()["id"]
        ResumableUpload.objects.filter(pk=uid).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.chunk(uid, 0, self.raw).status_code, 410)

    def test_invalid_extension_or_size_and_unsafename_rejected(self):
        self.assertEqual(self.start(filename="../../game.mp4").status_code, 400)
        self.assertEqual(self.start(filename="game.exe").status_code, 400)
        self.assertEqual(self.start(size=0).status_code, 400)

    def test_final_whole_file_hash_mismatch_does_not_publish(self):
        uid = self.start(sha="0"*64).json()["id"]
        self.assertEqual(self.chunk(uid, 0, self.raw).status_code, 200)
        self.assertEqual(self.client.post(reverse("resumable_finish", args=[uid])).status_code, 422)
        self.assertEqual(Game.objects.count(), 0)

    def test_browser_mode_without_whole_file_digest(self):
        uid = self.start(sha="").json()["id"]
        self.assertEqual(self.chunk(uid, 0, self.raw).status_code, 200)
        with patch("highlights.resumable_views.probe_duration", return_value=90):
            response = self.client.post(reverse("resumable_finish", args=[uid]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["sha256"], self.hash(self.raw))
