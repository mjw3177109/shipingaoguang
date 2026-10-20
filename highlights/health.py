"""Infrastructure-only service probes. No personal or queue data in responses."""
from django.db import connection
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from .operations import media_disk_free_bytes
from .worker_health import worker_is_alive
from django.conf import settings


def _status(ok):
    resp = HttpResponse(status=204 if ok else 503)
    resp["Cache-Control"] = "no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@require_GET
def health_live(request):
    # Intentionally no DB or storage dependency: process liveness only.
    return _status(True)


@require_GET
def health_ready(request):
    # Explicit dependency checks, no auto-creation of MEDIA_ROOT.
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            if cur.fetchone()[0] != 1:
                return _status(False)
        from pathlib import Path
        root = Path(settings.MEDIA_ROOT)
        ready = (root.is_dir() and
                 media_disk_free_bytes() >= settings.MEDIA_MIN_FREE_BYTES)
        return _status(ready)
    except Exception:
        return _status(False)


@require_GET
def health_worker(request):
    try:
        ready, _ = worker_is_alive()
        return _status(ready)
    except Exception:
        return _status(False)
