"""V1.2 live task polling and upload progress regression tests."""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Export, Game, Venue


class LiveTaskStatusTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp()
        override = override_settings(MEDIA_ROOT=self.tempdir)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tempdir, ignore_errors=True)
        User = get_user_model()
        self.owner = User.objects.create_user("live-owner", password="safe-password")
        self.other = User.objects.create_user("live-other", password="safe-password")
        self.venue = Venue.objects.create(owner=self.owner, name="Live Status Gym")
        self.game = Game.objects.create(
            venue=self.venue, title="Live status game",
            video=SimpleUploadedFile("live.mp4", b"authorized fixture" * 20),
            duration_seconds=60,
        )
        self.status_url = reverse("task_status", args=[self.game.pk])

    def test_task_status_is_private_and_includes_live_export_fragment(self):
        job = Export.objects.create(game=self.game, state=Export.State.QUEUED)
        self.client.force_login(self.owner)
        response = self.client.get(self.status_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        data = response.json()
        self.assertTrue(data["active"])
        self.assertEqual(data["game_state"], Game.State.UPLOADED)
        self.assertIn(f'id="export-row-{job.pk}"', data["exports_html"])
        self.assertIn("取消排队", data["exports_html"])
        self.assertIn("csrfmiddlewaretoken", data["exports_html"])
        self.assertNotIn('value="NOTPROVIDED"', data["exports_html"])

    def test_status_stops_polling_when_all_tasks_are_finished(self):
        job = Export.objects.create(
            game=self.game, state=Export.State.DONE,
            video=SimpleUploadedFile("finished.mp4", b"fixture-output"),
        )
        self.client.force_login(self.owner)
        response = self.client.get(self.status_url)
        data = response.json()
        self.assertFalse(data["active"])
        self.assertIn(f'id="export-row-{job.pk}"', data["exports_html"])
        self.assertIn(reverse("export_video", args=[self.game.pk, job.pk]),
                      data["exports_html"])

    def test_task_status_respects_owner_tenant_boundary(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.status_url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.status_url).status_code, 302)

    def test_dashboard_exposes_upload_progress_without_changing_upload_contract(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="upload-form"')
        self.assertContains(response, 'id="upload-progress"')
        self.assertContains(response, "upload-progress.js")

    def test_game_page_polls_without_replacing_video_element(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("game", args=[self.game.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="task-live-status"')
        self.assertContains(response, 'id="export-table-body"')
        self.assertContains(response, "task-status.js")
        self.assertContains(response, 'id="player"')
