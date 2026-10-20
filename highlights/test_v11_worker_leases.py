"""V1.1 regression tests for stale task recovery and worker-token fencing."""
import json
import shutil
import tempfile
from io import StringIO
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Export, Game, Venue
from .services import _update_export_under_lease
from .worker_leases import (
    WorkerLeaseLost, heartbeat_export, heartbeat_game, heartbeat_sampling,
)
from .management.commands.runworker import Command


class WorkerLeaseRecoveryTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tempdir)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        user = get_user_model().objects.create_user("lease-owner", password="test-password")
        venue = Venue.objects.create(owner=user, name="Lease Test Gym")
        self.game = Game.objects.create(
            venue=venue, title="Lease test video",
            video=SimpleUploadedFile("lease.mp4", b"test-video-bytes" * 300),
            duration_seconds=60,
        )
        self.old = timezone.now() - timedelta(hours=1)
        self.command = Command()

    def test_stale_export_is_requeued_and_old_token_is_fenced(self):
        job = Export.objects.create(
            game=self.game, state=Export.State.RUNNING,
            worker_started_at=self.old, worker_heartbeat_at=self.old,
            worker_token="old-worker-token",
        )
        counts = self.command._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        job.refresh_from_db()
        self.assertEqual(counts["export"], 1)
        self.assertEqual(job.state, Export.State.QUEUED)
        self.assertEqual(job.worker_recovery_count, 1)
        self.assertEqual(job.worker_token, "")
        self.assertIsNone(job.worker_heartbeat_at)
        self.assertIn("requeued", job.error)
        with self.assertRaises(WorkerLeaseLost):
            heartbeat_export(job.pk, "old-worker-token")

    def test_export_is_failed_after_automatic_recovery_budget(self):
        job = Export.objects.create(
            game=self.game, state=Export.State.RUNNING,
            worker_started_at=self.old, worker_heartbeat_at=self.old,
            worker_token="expired", worker_recovery_count=2,
        )
        counts = self.command._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        job.refresh_from_db()
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(job.state, Export.State.FAILED)
        self.assertEqual(job.worker_recovery_count, 2)
        self.assertIn("limit reached", job.error)
        self.assertEqual(job.worker_token, "")

    def test_stale_analysis_is_requeued_and_old_worker_cannot_heartbeat(self):
        Game.objects.filter(pk=self.game.pk).update(
            state=Game.State.ANALYZING, worker_started_at=self.old,
            worker_heartbeat_at=self.old, worker_token="old-analysis",
        )
        counts = self.command._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        self.game.refresh_from_db()
        self.assertEqual(counts["analysis"], 1)
        self.assertEqual(self.game.state, Game.State.QUEUED)
        self.assertEqual(self.game.worker_recovery_count, 1)
        with self.assertRaises(WorkerLeaseLost):
            heartbeat_game(self.game.pk, "old-analysis")

    def test_stale_annotation_sampler_is_requeued(self):
        Game.objects.filter(pk=self.game.pk).update(
            ball_sampling_state="running",
            ball_sampling_started_at=self.old,
            ball_sampling_heartbeat_at=self.old,
            ball_sampling_worker_token="old-sampler",
        )
        counts = self.command._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        self.game.refresh_from_db()
        self.assertEqual(counts["sampling"], 1)
        self.assertEqual(self.game.ball_sampling_state, "queued")
        self.assertEqual(self.game.ball_sampling_recovery_count, 1)
        with self.assertRaises(WorkerLeaseLost):
            heartbeat_sampling(self.game.pk, "old-sampler")

    def test_legacy_running_job_without_lease_timestamps_is_recovered(self):
        job = Export.objects.create(game=self.game, state=Export.State.RUNNING)
        Export.objects.filter(pk=job.pk).update(created_at=self.old)
        self.command._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        job.refresh_from_db()
        self.assertEqual(job.state, Export.State.QUEUED)
        self.assertEqual(job.worker_recovery_count, 1)

    def test_fresh_heartbeat_prevents_recovery_even_for_old_job(self):
        job = Export.objects.create(
            game=self.game, state=Export.State.RUNNING,
            worker_started_at=self.old, worker_heartbeat_at=timezone.now(),
            worker_token="active-token",
        )
        counts = self.command._recover_stale_tasks(stale_after_seconds=60, max_recoveries=2)
        job.refresh_from_db()
        self.assertEqual(counts["export"], 0)
        self.assertEqual(job.state, Export.State.RUNNING)
        self.assertEqual(job.worker_token, "active-token")
        heartbeat_export(job.pk, "active-token")

    def test_stale_worker_cannot_update_export_progress_after_reclaim(self):
        job = Export.objects.create(
            game=self.game, state=Export.State.RUNNING,
            worker_started_at=timezone.now(), worker_heartbeat_at=timezone.now(),
            worker_token="worker-A",
        )
        _update_export_under_lease(job, clips_done=1)
        job.refresh_from_db()
        self.assertEqual(job.clips_done, 1)
        Export.objects.filter(pk=job.pk).update(worker_token="worker-B")
        with self.assertRaises(WorkerLeaseLost):
            _update_export_under_lease(job, clips_done=2)

    def test_user_retry_resets_game_recovery_budget(self):
        from django.urls import reverse
        user = self.game.venue.owner
        self.client.force_login(user)
        Game.objects.filter(pk=self.game.pk).update(
            state=Game.State.FAILED, worker_recovery_count=2,
            worker_started_at=self.old, worker_heartbeat_at=self.old,
            worker_token="expired",
        )
        response = self.client.post(reverse("queue_analysis", args=[self.game.pk]),
                                    {"engine": "cpu"})
        self.assertEqual(response.status_code, 302)
        self.game.refresh_from_db()
        self.assertEqual(self.game.state, Game.State.QUEUED)
        self.assertEqual(self.game.worker_recovery_count, 0)
        self.assertEqual(self.game.worker_token, "")
        self.assertIsNone(self.game.worker_heartbeat_at)

    def test_manual_sampler_retry_resets_recovery_budget(self):
        from django.urls import reverse
        self.client.force_login(self.game.venue.owner)
        Game.objects.filter(pk=self.game.pk).update(
            ball_sampling_state="failed", ball_sampling_recovery_count=2,
            ball_sampling_started_at=self.old, ball_sampling_heartbeat_at=self.old,
            ball_sampling_worker_token="expired",
        )
        response = self.client.post(reverse("queue_ball_sampling", args=[self.game.pk]))
        self.assertEqual(response.status_code, 302)
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_state, "queued")
        self.assertEqual(self.game.ball_sampling_recovery_count, 0)
        self.assertEqual(self.game.ball_sampling_worker_token, "")
        self.assertIsNone(self.game.ball_sampling_heartbeat_at)


    def test_workerstatus_reports_stale_tasks_and_check_fails(self):
        job = Export.objects.create(
            game=self.game, state=Export.State.RUNNING,
            worker_started_at=self.old, worker_heartbeat_at=self.old,
            worker_token="expired-status",
        )
        output = StringIO()
        call_command("workerstatus", "--json", "--stale-after", "60", stdout=output)
        data = json.loads(output.getvalue())
        self.assertEqual(data["exports"]["stale_running"], 1)
        self.assertEqual(data["stale_total"], 1)
        with self.assertRaises(CommandError):
            call_command("workerstatus", "--check-stale", "--stale-after", "60")

    def test_worker_claims_export_with_unique_lease_token(self):
        from unittest.mock import patch

        job = Export.objects.create(game=self.game, state=Export.State.QUEUED)
        observed = {}

        def fake_render(claimed, heartbeat=None):
            observed["token"] = claimed.worker_token
            observed["state"] = claimed.state
            self.assertTrue(claimed.worker_started_at)
            heartbeat(force=True)
            Export.objects.filter(
                pk=claimed.pk, state=Export.State.RUNNING,
                worker_token=claimed.worker_token
            ).update(state=Export.State.DONE, worker_token="", worker_heartbeat_at=None)

        with patch("highlights.management.commands.runworker.render_export",
                   side_effect=fake_render):
            self.assertTrue(self.command._process_one())
        job.refresh_from_db()
        self.assertEqual(observed["state"], Export.State.RUNNING)
        self.assertTrue(observed["token"])
        self.assertEqual(job.state, Export.State.DONE)
        self.assertEqual(job.worker_token, "")

    def test_old_worker_error_cannot_overwrite_a_reclaimed_task(self):
        from unittest.mock import patch

        job = Export.objects.create(game=self.game, state=Export.State.QUEUED)

        def stale_render(claimed, heartbeat=None):
            Export.objects.filter(pk=claimed.pk).update(
                state=Export.State.QUEUED, worker_token="new-worker-token",
                worker_heartbeat_at=None, worker_started_at=None)
            raise RuntimeError("old worker should not mark new lease failed")

        with patch("highlights.management.commands.runworker.render_export",
                   side_effect=stale_render):
            self.command._process_one()
        job.refresh_from_db()
        self.assertEqual(job.state, Export.State.QUEUED)
        self.assertEqual(job.worker_token, "new-worker-token")
        self.assertNotIn("old worker should not mark new lease failed", job.error)
