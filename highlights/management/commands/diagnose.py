"""V1.3 read-only production preflight and honest CI diagnostics.

Creates/deletes a zero-byte probe in MEDIA_ROOT; never processes or exposes user media.
"""
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from highlights.operations import media_disk_free_bytes
from highlights.worker_health import worker_is_alive


def collect_diagnostics(*, skip_worker=False, production=False):
    checks = {}

    def record(name, ok, detail, *, status=None, metrics=None):
        item = {"status": status or ("PASS" if ok else "FAIL"), "detail": detail}
        if metrics is not None:
            item["metrics"] = metrics
        checks[name] = item

    try:
        out = StringIO()
        err = StringIO()
        call_command("check", stdout=out, stderr=err)
        record("django", True, "Django system checks passed")
    except Exception as exc:
        record("django", False, "Django checks failed: " + type(exc).__name__)

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("database probe returned an unexpected value")
        record("database", True, "Database query succeeded")
    except Exception as exc:
        record("database", False, "Database check failed: " + type(exc).__name__)

    if checks["database"]["status"] == "PASS":
        try:
            executor = MigrationExecutor(connection)
            conflicts = executor.loader.detect_conflicts()
            if conflicts:
                record("migrations", False, "Conflicting migration branches found",
                       metrics={"conflicts": len(conflicts)})
            else:
                pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
                record("migrations", not pending,
                       "No unapplied migrations" if not pending else "Unapplied migrations detected",
                       metrics={"unapplied_count": len(pending)})
        except Exception as exc:
            record("migrations", False, "Migration inspection failed: " + type(exc).__name__)
    else:
        record("migrations", False, "Cannot inspect migrations without a database")

    root = Path(settings.MEDIA_ROOT)
    try:
        if not root.is_dir():
            raise FileNotFoundError("MEDIA_ROOT is not a directory")
        with tempfile.NamedTemporaryFile(prefix=".basketcut-preflight-", dir=root) as probe:
            probe.write(b"")
            probe.flush()
        record("media_directory", True, "Media directory exists and accepts a temporary write probe")
    except (OSError, ValueError) as exc:
        record("media_directory", False, "Media directory is not writable: " + type(exc).__name__)

    try:
        free = media_disk_free_bytes()
        reserve = settings.MEDIA_MIN_FREE_BYTES
        record("media_disk", free >= reserve,
               "Media disk reserve satisfied" if free >= reserve else "Media disk below configured reserve",
               metrics={"free_bytes": free, "reserve_bytes": reserve})
    except Exception as exc:
        record("media_disk", False, "Disk inspection failed: " + type(exc).__name__)

    for executable in ("ffmpeg", "ffprobe"):
        name = executable
        try:
            found = shutil.which(executable)
            if not found:
                raise FileNotFoundError(executable)
            proc = subprocess.run(
                [executable, "-version"], capture_output=True, text=True,
                timeout=15, check=False)
            if proc.returncode:
                raise RuntimeError("non-zero version command")
            version = (proc.stdout.splitlines() or [""])[0][:160]
            record(name, bool(version), "Executable responded to -version",
                   metrics={"version": version})
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            record(name, False, "Unavailable or failed: " + type(exc).__name__)

    resource_values = {
        "max_upload_bytes": settings.MAX_UPLOAD_BYTES,
        "owner_max_raw_bytes": settings.OWNER_MAX_RAW_BYTES,
        "owner_max_active_exports": settings.OWNER_MAX_ACTIVE_EXPORTS,
        "export_temp_bytes_per_second": settings.EXPORT_TEMP_BYTES_PER_SECOND,
        "worker_health_max_age_seconds": settings.WORKER_HEALTH_MAX_AGE_SECONDS,
        "media_min_free_bytes": settings.MEDIA_MIN_FREE_BYTES,
    }
    positive = all(type(resource_values[key]) is int and resource_values[key] > 0 for key in (
        "max_upload_bytes", "owner_max_raw_bytes", "owner_max_active_exports",
        "export_temp_bytes_per_second", "worker_health_max_age_seconds"))
    positive = positive and type(resource_values["media_min_free_bytes"]) is int and resource_values["media_min_free_bytes"] >= 0
    record("resource_limits", positive, "Configured operational limits are valid" if positive
           else "Invalid resource limit (positive values required except reserve)",
           metrics=resource_values)

    if skip_worker:
        record("worker", True, "Worker probe explicitly skipped", status="SKIP")
        record("queue", True, "Queue probe explicitly skipped", status="SKIP")
    else:
        try:
            alive, age = worker_is_alive()
            record("worker", alive, "Worker heartbeat is fresh" if alive
                   else "Worker heartbeat is missing or stale",
                   metrics={"alive": alive, "age_seconds": age,
                            "max_age_seconds": settings.WORKER_HEALTH_MAX_AGE_SECONDS})
        except Exception as exc:
            record("worker", False, "Worker heartbeat check failed: " + type(exc).__name__)
        try:
            out = StringIO()
            call_command("workerstatus", "--json", stdout=out)
            status = json.loads(out.getvalue())
            stale = status["stale_total"]
            record("queue", stale == 0, "No stale running jobs" if not stale
                   else "Stale running jobs need inspection",
                   metrics={"stale_total": stale, "analysis": status["analysis"],
                            "exports": status["exports"],
                            "annotation_sampling": status["annotation_sampling"]})
        except Exception as exc:
            record("queue", False, "Queue inspection failed: " + type(exc).__name__)

    if production:
        secure = (not settings.DEBUG and settings.SESSION_COOKIE_SECURE
                  and settings.CSRF_COOKIE_SECURE and bool(settings.ALLOWED_HOSTS)
                  and "*" not in settings.ALLOWED_HOSTS)
        record("production_security", secure,
               "Production security baseline enabled" if secure else
               "DEBUG, HTTPS cookie flags or ALLOWED_HOSTS not production safe")

    failures = [key for key, item in checks.items() if item["status"] == "FAIL"]
    return {
        "schema_version": 1, "basketcut_version": "1.3",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall": "PASS" if not failures else "FAIL",
        "strict_checks_pass": not failures,
        "mode": "production" if production else "deployment",
        "checks": checks, "failed_checks": failures,
        "limitations": [
            "This is a point-in-time local preflight, not continuous monitoring.",
            "Disk free space is not reserved atomically across concurrent jobs.",
            "A fresh heartbeat does not prove that queued exports will complete.",
            "No basket detection accuracy or real full-game throughput is measured.",
        ],
    }


class Command(BaseCommand):
    help = "Audit migrations, media, FFmpeg, worker, queue and resource limits (no user video read)."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument("--strict", action="store_true",
                            help="Exit non-zero when any preflight check fails.")
        parser.add_argument("--production", action="store_true",
                            help="Also require production-safe Django cookie/debug/host baseline.")
        parser.add_argument("--skip-worker", action="store_true",
                            help="Only for offline installation; skips worker and queue checks.")

    def handle(self, *args, **options):
        result = collect_diagnostics(skip_worker=options["skip_worker"],
                                     production=options["production"])
        if options["as_json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self.stdout.write("BasketCut V1.3 deployment preflight: " + result["overall"])
            for key, value in result["checks"].items():
                self.stdout.write(f"[{value['status']}] {key}: {value['detail']}")
        if options["strict"] and result["failed_checks"]:
            raise CommandError("Preflight failed: " + ", ".join(result["failed_checks"]))
