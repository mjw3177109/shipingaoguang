import time
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.db.models import F, Q
from django.utils import timezone

from highlights.models import Candidate, Export, Game, BallFrameSuggestion
from highlights.services import analyze_game, render_export
from highlights.vision.cpu import scan_cpu_video
from highlights.vision.annotation_sampler import scan_annotation_frames
from highlights.worker_leases import (
    WorkerLeaseLost, heartbeat_export, heartbeat_game, heartbeat_sampling,
    throttled_heartbeat,
)


class Command(BaseCommand):
    help = "Run a fenced CPU worker; stale jobs are safely recovered after their lease expires."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stale_after_seconds = 900
        self.max_recoveries = 2

    def add_arguments(self, parser):
        parser.add_argument("--poll", type=float, default=2.0)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--stale-after", type=int, default=900,
                            help="Seconds without a heartbeat before a running task is recovered (default: 900).")
        parser.add_argument("--max-recoveries", type=int, default=2,
                            help="Automatic stale-worker recoveries before marking a task failed (default: 2).")

    def handle(self, *args, **options):
        self.stale_after_seconds = max(1, int(options["stale_after"]))
        self.max_recoveries = max(0, int(options["max_recoveries"]))
        self.stdout.write(
            f"BasketCut worker running (stale_after={self.stale_after_seconds}s, "
            f"max_recoveries={self.max_recoveries})")
        while True:
            close_old_connections()
            try:
                ran = self._process_one()
            except Exception as exc:
                self.stderr.write(f"Unexpected worker error: {type(exc).__name__}: {exc}")
                ran = False
            if options["once"]:
                break
            if not ran:
                time.sleep(max(.2, options["poll"]))

    @staticmethod
    def _stale_filter(heartbeat_field, started_field, cutoff):
        # The third clause recovers tasks left running by a pre-V1.1 worker
        # before lease timestamps existed. State predicates still fence updates.
        return (
            Q(**{f"{heartbeat_field}__lt": cutoff})
            | Q(**{f"{heartbeat_field}__isnull": True,
                   f"{started_field}__lt": cutoff})
            | Q(**{f"{heartbeat_field}__isnull": True,
                   f"{started_field}__isnull": True,
                   "created_at__lt": cutoff})
        )

    def _recover_stale_tasks(self, stale_after_seconds=None, max_recoveries=None):
        """Requeue expired leases; an old worker cannot commit after its token is revoked."""
        stale_after_seconds = self.stale_after_seconds if stale_after_seconds is None else max(1, int(stale_after_seconds))
        max_recoveries = self.max_recoveries if max_recoveries is None else max(0, int(max_recoveries))
        cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
        recovered = {"analysis": 0, "export": 0, "sampling": 0, "failed": 0}

        specs = [
            (Game, "state", Game.State.ANALYZING, "queued", "worker_heartbeat_at",
             "worker_started_at", "worker_token", "worker_recovery_count", "error",
             "analysis", 1200),
            (Export, "state", Export.State.RUNNING, Export.State.QUEUED, "worker_heartbeat_at",
             "worker_started_at", "worker_token", "worker_recovery_count", "error",
             "export", 1200),
            (Game, "ball_sampling_state", "running", "queued", "ball_sampling_heartbeat_at",
             "ball_sampling_started_at", "ball_sampling_worker_token",
             "ball_sampling_recovery_count", "ball_sampling_error", "sampling", 600),
        ]
        for model, state_field, running_state, queued_state, heartbeat_field, started_field, token_field, count_field, error_field, key, error_limit in specs:
            stale = self._stale_filter(heartbeat_field, started_field, cutoff)
            ids = list(model.objects.filter(**{state_field: running_state})
                       .filter(stale).values_list("pk", flat=True))
            for pk in ids:
                base = model.objects.filter(pk=pk, **{state_field: running_state}).filter(stale)
                current = base.values_list(count_field, flat=True).first()
                if current is None:
                    continue
                if current < max_recoveries:
                    message = (
                        f"Worker heartbeat expired; automatically requeued "
                        f"(recovery {current + 1}/{max_recoveries}).")
                    changes = {
                        state_field: queued_state,
                        token_field: "",
                        heartbeat_field: None,
                        started_field: None,
                        count_field: F(count_field) + 1,
                        error_field: message[:error_limit],
                    }
                    updated = base.filter(**{f"{count_field}__lt": max_recoveries}).update(**changes)
                    if updated:
                        recovered[key] += 1
                else:
                    message = (
                        f"Worker heartbeat expired {current} times; automatic recovery limit reached. "
                        "Inspect worker logs and retry the task.")
                    changes = {
                        state_field: "failed",
                        token_field: "",
                        heartbeat_field: None,
                        error_field: message[:error_limit],
                    }
                    updated = base.filter(**{f"{count_field}__gte": max_recoveries}).update(**changes)
                    if updated:
                        recovered["failed"] += 1
                        recovered[key] += 1
        total = recovered["analysis"] + recovered["export"] + recovered["sampling"]
        if total:
            self.stdout.write("Recovered stale tasks: " + ", ".join(
                f"{key}={value}" for key, value in recovered.items() if value))
        return recovered

    def _process_one(self):
        self._recover_stale_tasks()
        game = Game.objects.filter(state=Game.State.QUEUED).order_by("created_at").first()
        if game:
            token = uuid.uuid4().hex
            now = timezone.now()
            claimed = Game.objects.filter(pk=game.pk, state=Game.State.QUEUED).update(
                state=Game.State.ANALYZING, worker_started_at=now,
                worker_heartbeat_at=now, worker_token=token, error="")
            if not claimed:
                return True
            game.refresh_from_db()
            heartbeat = throttled_heartbeat(lambda: heartbeat_game(game.pk, token))
            try:
                heartbeat(force=True)
                if game.analysis_engine == "cpu":
                    result = scan_cpu_video(
                        game.video.path, hoops=game.hoops, heartbeat=heartbeat)
                    heartbeat(force=True)
                    with transaction.atomic():
                        locked = Game.objects.select_for_update().get(pk=game.pk)
                        if locked.state != Game.State.ANALYZING or locked.worker_token != token:
                            raise WorkerLeaseLost(f"Game {game.pk} analysis lease lost before commit.")
                        Candidate.objects.filter(game=game, source__in=(
                            "cpu_ball_activity", "cpu_hoop_activity"), review="pending").delete()
                        Candidate.objects.bulk_create([
                            Candidate(game=game, start_seconds=c["start_seconds"],
                                      end_seconds=c["end_seconds"], source=c["source"],
                                      energy_score=c["score"], note=c["note"])
                            for c in result["candidates"]
                        ])
                        changed = Game.objects.filter(
                            pk=game.pk, state=Game.State.ANALYZING, worker_token=token
                        ).update(duration_seconds=result["video_duration_seconds"],
                                 state=Game.State.READY, error="", worker_token="",
                                 worker_heartbeat_at=None)
                        if changed != 1:
                            raise WorkerLeaseLost(f"Game {game.pk} analysis lease lost during commit.")
                    self.stdout.write(
                        f"Game {game.pk}: {len(result['candidates'])} CPU visual candidates; "
                        f"processed_frames={result['analysis']['frames_processed']}")
                else:
                    count = analyze_game(game, heartbeat=heartbeat)
                    self.stdout.write(f"Game {game.pk}: {count} audio proposals")
            except WorkerLeaseLost as exc:
                self.stderr.write(str(exc))
            except Exception as exc:
                updated = Game.objects.filter(
                    pk=game.pk, state=Game.State.ANALYZING, worker_token=token
                ).update(state=Game.State.FAILED, error=str(exc)[:1200],
                         worker_token="", worker_heartbeat_at=None)
                if updated:
                    self.stderr.write(f"Game {game.pk} failed: {exc}")
                else:
                    self.stderr.write(f"Game {game.pk}: old worker stopped after losing its lease.")
            return True

        export = Export.objects.filter(state=Export.State.QUEUED).select_related(
            "game").order_by("created_at").first()
        if export:
            token = uuid.uuid4().hex
            now = timezone.now()
            claimed = Export.objects.filter(pk=export.pk, state=Export.State.QUEUED).update(
                state=Export.State.RUNNING, worker_started_at=now,
                worker_heartbeat_at=now, worker_token=token, clips_done=0, error="")
            if not claimed:
                return True
            export.refresh_from_db()
            heartbeat = throttled_heartbeat(lambda: heartbeat_export(export.pk, token))
            try:
                heartbeat(force=True)
                render_export(export, heartbeat=heartbeat)
                self.stdout.write(f"Export {export.pk} done")
            except WorkerLeaseLost as exc:
                self.stderr.write(str(exc))
            except Exception as exc:
                updated = Export.objects.filter(
                    pk=export.pk, state=Export.State.RUNNING, worker_token=token
                ).update(state=Export.State.FAILED, error=str(exc)[:1200],
                         worker_token="", worker_heartbeat_at=None)
                if updated:
                    self.stderr.write(f"Export {export.pk} failed: {exc}")
                else:
                    self.stderr.write(f"Export {export.pk}: old worker stopped after losing its lease.")
            return True

        queued = Game.objects.filter(ball_sampling_state="queued").order_by("created_at").first()
        if queued:
            token = uuid.uuid4().hex
            now = timezone.now()
            claimed = Game.objects.filter(pk=queued.pk, ball_sampling_state="queued").update(
                ball_sampling_state="running", ball_sampling_started_at=now,
                ball_sampling_heartbeat_at=now, ball_sampling_worker_token=token,
                ball_sampling_error="")
            if not claimed:
                return True
            queued.refresh_from_db()
            heartbeat = throttled_heartbeat(
                lambda: heartbeat_sampling(queued.pk, token))
            try:
                heartbeat(force=True)
                outcome = scan_annotation_frames(
                    queued.video.path, limit=24, max_samples=96,
                    sampling_round=max(0, queued.ball_sampling_round - 1),
                    heartbeat=heartbeat)
                heartbeat(force=True)
                with transaction.atomic():
                    locked = Game.objects.select_for_update().get(pk=queued.pk)
                    if (locked.ball_sampling_state != "running"
                            or locked.ball_sampling_worker_token != token):
                        raise WorkerLeaseLost(
                            f"Game {queued.pk} annotation sampler lease lost before commit.")
                    human = list(locked.ball_frames.values_list("time_seconds", flat=True))
                    prior = list(locked.ball_suggestions.values_list("time_seconds", flat=True))
                    occupied = human + prior
                    fresh = []
                    for suggestion in outcome["suggestions"]:
                        if len(prior) + len(fresh) >= 300:
                            break
                        t = suggestion["time_seconds"]
                        if any(abs(t - prev) < .25 for prev in occupied):
                            continue
                        occupied.append(t)
                        fresh.append(BallFrameSuggestion(
                            game=locked, time_seconds=t,
                            reason=suggestion["reason"], score=suggestion["score"]))
                    BallFrameSuggestion.objects.bulk_create(fresh, ignore_conflicts=True)
                    changed = Game.objects.filter(
                        pk=queued.pk, ball_sampling_state="running",
                        ball_sampling_worker_token=token
                    ).update(ball_sampling_state="ready", ball_sampling_error="",
                             ball_sampling_worker_token="", ball_sampling_heartbeat_at=None)
                    if changed != 1:
                        raise WorkerLeaseLost(
                            f"Game {queued.pk} annotation sampler lease lost during commit.")
                self.stdout.write(
                    f"Game {queued.pk}: sampling round {queued.ball_sampling_round}, "
                    f"added {len(fresh)}, pending {len(prior) + len(fresh)} (unverified)")
            except WorkerLeaseLost as exc:
                self.stderr.write(str(exc))
            except Exception as exc:
                updated = Game.objects.filter(
                    pk=queued.pk, ball_sampling_state="running",
                    ball_sampling_worker_token=token
                ).update(ball_sampling_state="failed",
                         ball_sampling_error=str(exc)[:600],
                         ball_sampling_worker_token="", ball_sampling_heartbeat_at=None)
                if updated:
                    self.stderr.write(f"Game {queued.pk} annotation sampling failed: {exc}")
                else:
                    self.stderr.write(f"Game {queued.pk}: old sampler stopped after losing its lease.")
            return True
        return False
