"""Lightweight singleton Worker heartbeat for operational diagnostics."""
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import WorkerPulse

WORKER_KEY = "cpu-primary"


def pulse_worker(*, started=False, completed=False):
    now = timezone.now()
    record, created = WorkerPulse.objects.get_or_create(
        key=WORKER_KEY,
        defaults={"last_seen_at": now, "started_at": now})
    if not created:
        changes = {"last_seen_at": now}
        if started:
            changes["started_at"] = now
        if completed:
            changes["task_count"] = F("task_count") + 1
        WorkerPulse.objects.filter(pk=record.pk).update(**changes)
    elif completed:
        WorkerPulse.objects.filter(pk=record.pk).update(task_count=F("task_count") + 1)


def worker_is_alive(*, now=None):
    now = now or timezone.now()
    pulse = WorkerPulse.objects.filter(key=WORKER_KEY).values(
        "last_seen_at", "task_count").first()
    if not pulse:
        return False, None
    age = max(0, int((now - pulse["last_seen_at"]).total_seconds()))
    return age <= settings.WORKER_HEALTH_MAX_AGE_SECONDS, age
