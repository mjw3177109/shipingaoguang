"""Fenced worker leases shared by analysis, annotation sampling and export jobs."""
import time

from django.utils import timezone

from .models import Export, Game


class WorkerLeaseLost(RuntimeError):
    """Raised when a task is no longer owned by the current worker."""


def heartbeat_export(export_id, token):
    changed = Export.objects.filter(
        pk=export_id, state=Export.State.RUNNING, worker_token=token
    ).update(worker_heartbeat_at=timezone.now())
    if changed != 1:
        raise WorkerLeaseLost(f"Export {export_id} lease lost; stopping old worker.")


def heartbeat_game(game_id, token):
    changed = Game.objects.filter(
        pk=game_id, state=Game.State.ANALYZING, worker_token=token
    ).update(worker_heartbeat_at=timezone.now())
    if changed != 1:
        raise WorkerLeaseLost(f"Game {game_id} analysis lease lost; stopping old worker.")


def heartbeat_sampling(game_id, token):
    changed = Game.objects.filter(
        pk=game_id, ball_sampling_state="running", ball_sampling_worker_token=token
    ).update(ball_sampling_heartbeat_at=timezone.now())
    if changed != 1:
        raise WorkerLeaseLost(f"Game {game_id} sampler lease lost; stopping old worker.")


def throttled_heartbeat(callback, *, interval_seconds=10):
    """Limit heartbeat writes while keeping a blocking subprocess lease alive."""
    last = [0.0]

    def beat(force=False):
        now = time.monotonic()
        if force or now - last[0] >= interval_seconds:
            callback()
            last[0] = now

    return beat
