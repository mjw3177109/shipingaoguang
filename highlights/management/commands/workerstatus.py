"""Operational queue snapshot for the single-process BasketCut CPU worker."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from highlights.models import Export, Game
from highlights.worker_health import worker_is_alive


class Command(BaseCommand):
    help = "Show queued/running/failed jobs and detect stale worker leases."

    def add_arguments(self, parser):
        parser.add_argument("--stale-after", type=int, default=900,
                            help="Treat a running task without a heartbeat for this many seconds as stale.")
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="Print machine-readable JSON.")
        parser.add_argument("--check-stale", action="store_true",
                            help="Exit non-zero when a stale running task is detected.")
        parser.add_argument("--check-worker", action="store_true",
                            help="Exit non-zero when the worker heartbeat is unavailable or stale.")

    @staticmethod
    def _stale_filter(heartbeat, started, cutoff):
        return (
            Q(**{f"{heartbeat}__lt": cutoff})
            | Q(**{f"{heartbeat}__isnull": True, f"{started}__lt": cutoff})
            | Q(**{f"{heartbeat}__isnull": True, f"{started}__isnull": True,
                   "created_at__lt": cutoff})
        )

    def handle(self, *args, **options):
        stale_after = max(1, int(options["stale_after"]))
        cutoff = timezone.now() - timedelta(seconds=stale_after)
        data = {
            "checked_at": timezone.now().isoformat(),
            "stale_after_seconds": stale_after,
            "analysis": {
                "queued": Game.objects.filter(state=Game.State.QUEUED).count(),
                "running": Game.objects.filter(state=Game.State.ANALYZING).count(),
                "stale_running": Game.objects.filter(state=Game.State.ANALYZING).filter(
                    self._stale_filter("worker_heartbeat_at","worker_started_at",cutoff)).count(),
                "failed": Game.objects.filter(state=Game.State.FAILED).count(),
            },
            "exports": {
                "queued": Export.objects.filter(state=Export.State.QUEUED).count(),
                "running": Export.objects.filter(state=Export.State.RUNNING).count(),
                "stale_running": Export.objects.filter(state=Export.State.RUNNING).filter(
                    self._stale_filter("worker_heartbeat_at","worker_started_at",cutoff)).count(),
                "failed": Export.objects.filter(state=Export.State.FAILED).count(),
            },
            "annotation_sampling": {
                "queued": Game.objects.filter(ball_sampling_state="queued").count(),
                "running": Game.objects.filter(ball_sampling_state="running").count(),
                "stale_running": Game.objects.filter(ball_sampling_state="running").filter(
                    self._stale_filter("ball_sampling_heartbeat_at",
                                       "ball_sampling_started_at",cutoff)).count(),
                "failed": Game.objects.filter(ball_sampling_state="failed").count(),
            },
        }
        alive, age_seconds = worker_is_alive()
        data["worker"] = {
            "alive": alive,
            "age_seconds": age_seconds,
        }
        stale_total = sum(group["stale_running"] for key, group in data.items()
                          if key in ("analysis","exports","annotation_sampling"))
        data["stale_total"] = stale_total
        if options["as_json"]:
            self.stdout.write(json.dumps(data,ensure_ascii=False,indent=2))
        else:
            self.stdout.write(f"BasketCut worker queue snapshot · {data['checked_at']}")
            self.stdout.write(f"Stale threshold: {stale_after}s · stale tasks: {stale_total}")
            self.stdout.write(
                f"Worker heartbeat: {'OK' if alive else 'OFFLINE'} "
                f"(age={age_seconds if age_seconds is not None else 'unknown'}s)")
            for key in ("analysis","exports","annotation_sampling"):
                group=data[key]
                self.stdout.write(
                    f"{key}: queued={group['queued']} running={group['running']} "
                    f"stale={group['stale_running']} failed={group['failed']}")
        if options["check_stale"] and stale_total:
            raise CommandError(f"Detected {stale_total} stale running task(s); inspect runworker logs.")
        if options["check_worker"] and not alive:
            raise CommandError("CPU Worker heartbeat is absent or stale. Restart/check the worker container.")
