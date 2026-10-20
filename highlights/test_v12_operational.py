"""V1.2 resource-budget, health and heartbeat regression checks."""
import json
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Candidate, Export, Game, Venue, WorkerPulse
from .operations import (
    InsufficientStorage, require_media_space, export_temp_bytes,
    owner_raw_media_bytes,
)
from .worker_health import pulse_worker, worker_is_alive


class V12OperationsTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        self.settings_override = override_settings(MEDIA_ROOT=self.tempdir)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        users = get_user_model()
        self.owner = users.objects.create_user("v12_owner", password="1234569!xyz")
        self.other = users.objects.create_user("v12_other", password="1234569!xyz")
        self.venue = Venue.objects.create(owner=self.owner, name="My court")
        self.game = Game.objects.create(
            venue=self.venue, title="Existing game", duration_seconds=60,
            video=SimpleUploadedFile("source.mp4", b"A" * 64))
        self.clip = Candidate.objects.create(
            game=self.game, start_seconds=1, end_seconds=3,
            source="manual", review="approved")
        self.client.force_login(self.owner)

    def test_health_probes_return_only_http_status(self):
        self.assertEqual(self.client.get(reverse("health_live")).status_code, 204)
        ready = self.client.get(reverse("health_ready"))
        self.assertEqual(ready.status_code, 204)
        self.assertEqual(bytes(ready.content), b"")
        self.assertEqual(ready["Cache-Control"], "no-store")
        self.assertEqual(self.client.get(reverse("health_worker")).status_code, 503)

    def test_health_ready_fails_closed_when_db_fails_or_space_low(self):
        with patch("highlights.health.connection.cursor", side_effect=RuntimeError("db down")):
            self.assertEqual(self.client.get(reverse("health_ready")).status_code, 503)
        with patch("highlights.health.media_disk_free_bytes", return_value=0):
            self.assertEqual(self.client.get(reverse("health_ready")).status_code, 503)
        with override_settings(MEDIA_ROOT=self.tempdir + "/not-created"):
            self.assertEqual(self.client.get(reverse("health_ready")).status_code, 503)

    def test_worker_pulse_becomes_live_then_stale(self):
        pulse_worker(started=True)
        self.assertEqual(self.client.get(reverse("health_worker")).status_code, 204)
        alive, age = worker_is_alive()
        self.assertTrue(alive)
        self.assertIsNotNone(age)
        pulse_worker(completed=True)
        pulse = WorkerPulse.objects.get(key="cpu-primary")
        self.assertEqual(pulse.task_count, 1)
        WorkerPulse.objects.filter(pk=pulse.pk).update(
            last_seen_at=timezone.now()-timedelta(hours=2))
        self.assertEqual(self.client.get(reverse("health_worker")).status_code, 503)

    def test_workerstatus_check_worker_fails_when_missing_and_succeeds_when_recent(self):
        with self.assertRaises(CommandError):
            call_command("workerstatus", "--check-worker", stdout=StringIO())
        pulse_worker(started=True)
        target = StringIO()
        call_command("workerstatus", "--json", "--check-worker", stdout=target)
        self.assertTrue(json.loads(target.getvalue())["worker"]["alive"])

    def test_guard_rejects_low_free_space_without_writing_file(self):
        with patch("highlights.operations.media_disk_free_bytes", return_value=200):
            with self.assertRaises(InsufficientStorage):
                require_media_space(150, reserve_bytes=60)
            require_media_space(130, reserve_bytes=60)
        with self.assertRaises(ValueError):
            require_media_space(-1)
        with self.assertRaises(ValueError):
            require_media_space(0, reserve_bytes=-1)

    def test_export_temp_budget_is_positive_and_monotonic(self):
        a = export_temp_bytes(2)
        b = export_temp_bytes(250)
        self.assertGreaterEqual(a, 64*1024*1024)
        self.assertGreater(b, a)
        with self.assertRaises(ValueError):
            export_temp_bytes(float("nan"))

    def test_owner_raw_media_quota_is_isolated(self):
        self.assertEqual(owner_raw_media_bytes(self.owner), 64)
        self.assertEqual(owner_raw_media_bytes(self.other), 0)

    def test_upload_rejects_full_quota_before_persisting_file(self):
        self.client.force_login(self.owner)
        url=reverse("upload_game")
        with override_settings(OWNER_MAX_RAW_BYTES=90, MEDIA_MIN_FREE_BYTES=0):
            with patch("highlights.views.probe_duration", return_value=50):
                result=self.client.post(url, {
                    "venue_id": self.venue.pk, "title": "New game",
                    "video": SimpleUploadedFile("new.mp4", b"B" * 40)})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(Game.objects.filter(venue=self.venue).count(), 1)

    def test_upload_rejects_disk_pressure_before_file_save(self):
        url=reverse("upload_game")
        with patch("highlights.operations.media_disk_free_bytes", return_value=20):
            with override_settings(MEDIA_MIN_FREE_BYTES=10, OWNER_MAX_RAW_BYTES=100000):
                result=self.client.post(url, {
                    "venue_id": self.venue.pk, "title": "Blocked",
                    "video": SimpleUploadedFile("upload.mp4", b"Y" * 24)})
        self.assertEqual(result.status_code, 302)
        self.assertFalse(Game.objects.filter(title="Blocked").exists())

    def test_upload_approved_when_both_limits_pass(self):
        url=reverse("upload_game")
        with override_settings(MEDIA_MIN_FREE_BYTES=0, OWNER_MAX_RAW_BYTES=120):
            with patch("highlights.views.probe_duration", return_value=90):
                result=self.client.post(url, {
                    "venue_id": self.venue.pk, "title": "Allowed",
                    "video": SimpleUploadedFile("upload.mp4", b"N" * 50)})
        self.assertEqual(result.status_code, 302)
        self.assertTrue(Game.objects.filter(title="Allowed").exists())

    def test_active_export_quota_applies_across_owner_venues(self):
        second_venue=Venue.objects.create(owner=self.owner, name="Court 2")
        other_game=Game.objects.create(venue=second_venue, title="Other match",
            duration_seconds=60,
            video=SimpleUploadedFile("other.mp4", b"V" * 80))
        Export.objects.create(game=other_game, state=Export.State.QUEUED)
        url=reverse("queue_export",args=[self.game.pk])
        with override_settings(OWNER_MAX_ACTIVE_EXPORTS=1):
            response=self.client.post(url, {"player_label":"","audio_mode":"silent"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Export.objects.filter(game=self.game).count(), 0)

    def test_export_unsafe_disk_budget_blocks_before_ffmpeg(self):
        from .export_plan import create_plan
        from .services import render_export

        plan, digest = create_plan(self.game, "")
        job=Export.objects.create(
            game=self.game, plan=plan, plan_sha256=digest, clips_total=1)
        with patch("highlights.services.require_media_space",
                   side_effect=InsufficientStorage("disk full")):
            with self.assertRaises(InsufficientStorage):
                render_export(job)
        job.refresh_from_db()
        self.assertFalse(job.video)
        self.assertEqual(job.state, Export.State.QUEUED)
