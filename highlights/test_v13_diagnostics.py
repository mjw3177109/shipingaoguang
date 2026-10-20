"""V1.3 diagnostic invariants: no false PASS when dependencies fail."""
import json
import shutil
import tempfile
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from highlights.models import WorkerPulse
from highlights.worker_health import pulse_worker


class V13DiagnosticsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="basketcut-v13-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        scope = override_settings(MEDIA_ROOT=self.tmp, MEDIA_MIN_FREE_BYTES=0)
        scope.enable()
        self.addCleanup(scope.disable)

    def diagnose(self, *flags):
        output = StringIO()
        try:
            call_command("diagnose", "--json", *flags, stdout=output)
        except CommandError:
            result = json.loads(output.getvalue())
            return result, True
        return json.loads(output.getvalue()), False

    def test_healthy_database_and_migrations(self):
        pulse_worker(started=True)
        report, raised = self.diagnose("--strict")
        self.assertFalse(raised, report["failed_checks"])
        self.assertEqual(report["overall"], "PASS")
        for name in ("django", "database", "migrations", "media_directory",
                     "media_disk", "ffmpeg", "ffprobe", "worker", "queue", "resource_limits"):
            self.assertEqual(report["checks"][name]["status"], "PASS", name)

    def test_missing_worker_fails_strict_but_json_is_still_emitted(self):
        report, raised = self.diagnose("--strict")
        self.assertTrue(raised)
        self.assertIn("worker", report["failed_checks"])
        self.assertFalse(report["checks"]["worker"]["metrics"]["alive"])

    def test_stale_worker_fails(self):
        pulse_worker(started=True)
        WorkerPulse.objects.update(last_seen_at=timezone.now() - timedelta(hours=2))
        report, _ = self.diagnose()
        self.assertEqual(report["checks"]["worker"]["status"], "FAIL")

    def test_missing_media_directory_fails(self):
        pulse_worker(started=True)
        with override_settings(MEDIA_ROOT=self.tmp + "/missing"):
            report, raised = self.diagnose("--strict")
        self.assertTrue(raised)
        self.assertIn("media_directory", report["failed_checks"])

    def test_disk_below_reserve_fails(self):
        pulse_worker(started=True)
        with override_settings(MEDIA_MIN_FREE_BYTES=500):
            with patch("highlights.management.commands.diagnose.media_disk_free_bytes",
                       return_value=499):
                report, raised = self.diagnose("--strict")
        self.assertTrue(raised)
        self.assertIn("media_disk", report["failed_checks"])

    def test_no_ffmpeg_fails_and_json_does_not_expose_media_paths(self):
        pulse_worker(started=True)
        with patch("highlights.management.commands.diagnose.shutil.which", return_value=None):
            report, raised = self.diagnose("--strict")
        self.assertTrue(raised)
        self.assertIn("ffmpeg", report["failed_checks"])
        self.assertIn("ffprobe", report["failed_checks"])
        self.assertNotIn(self.tmp, json.dumps(report))

    def test_worker_skip_is_explicit_and_production_guard_blocks_debug(self):
        report, raised = self.diagnose("--skip-worker", "--strict")
        self.assertFalse(raised)
        self.assertEqual(report["checks"]["worker"]["status"], "SKIP")
        report, raised = self.diagnose("--skip-worker", "--production", "--strict")
        self.assertTrue(raised)
        self.assertIn("production_security", report["failed_checks"])

    def test_bad_resource_value_fails(self):
        pulse_worker(started=True)
        with override_settings(OWNER_MAX_ACTIVE_EXPORTS=0):
            report, raised = self.diagnose("--strict")
        self.assertTrue(raised)
        self.assertIn("resource_limits", report["failed_checks"])
