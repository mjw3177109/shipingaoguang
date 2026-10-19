import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from highlights.models import Export, Game, BallFrameSuggestion
from highlights.services import analyze_game, render_export
from highlights.vision.cpu import scan_cpu_video
from highlights.vision.annotation_sampler import scan_annotation_frames
from highlights.models import Candidate


class Command(BaseCommand):
    help = "Run a single CPU analysis/render worker; use one process for this MVP."

    def add_arguments(self, parser):
        parser.add_argument("--poll", type=float, default=2.0)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write("BasketCut worker running")
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

    def _process_one(self):
        game = Game.objects.filter(state="queued").order_by("created_at").first()
        if game:
            if not Game.objects.filter(pk=game.pk, state="queued").update(state="analyzing"):
                return True
            try:
                if game.analysis_engine == "cpu":
                    result = scan_cpu_video(game.video.path, hoops=game.hoops)
                    Candidate.objects.filter(game=game, source__in=(
                        "cpu_ball_activity", "cpu_hoop_activity"), review="pending").delete()
                    Candidate.objects.bulk_create([
                        Candidate(game=game, start_seconds=c["start_seconds"],
                                  end_seconds=c["end_seconds"], source=c["source"],
                                  energy_score=c["score"], note=c["note"])
                        for c in result["candidates"]
                    ])
                    game.duration_seconds = result["video_duration_seconds"]
                    game.state = "ready"
                    game.error = ""
                    game.save(update_fields=["duration_seconds", "state", "error"])
                    count = len(result["candidates"])
                    self.stdout.write(f"Game {game.pk}: {count} CPU visual candidates; "
                                      f"processed_frames={result['analysis']['frames_processed']}")
                else:
                    count = analyze_game(game)
                    self.stdout.write(f"Game {game.pk}: {count} audio proposals")
            except Exception as exc:
                Game.objects.filter(pk=game.pk).update(state="failed", error=str(exc)[:1200])
                self.stderr.write(f"Game {game.pk} failed: {exc}")
            return True
        export = Export.objects.filter(state="queued").select_related("game").order_by("created_at").first()
        if export:
            if not Export.objects.filter(pk=export.pk, state="queued").update(state="running"):
                return True
            try:
                render_export(export)
                self.stdout.write(f"Export {export.pk} done")
            except Exception as exc:
                Export.objects.filter(pk=export.pk).update(state="failed", error=str(exc)[:1200])
                self.stderr.write(f"Export {export.pk} failed: {exc}")
            return True
        queued = Game.objects.filter(ball_sampling_state="queued").order_by("created_at").first()
        if queued:
            if not Game.objects.filter(pk=queued.pk,ball_sampling_state="queued").update(ball_sampling_state="running"):
                return True
            try:
                outcome = scan_annotation_frames(queued.video.path,limit=24,max_samples=96)
                source_times=[(s["time_seconds"],s["reason"],s["score"]) for s in outcome["suggestions"]]
                existing=set(round(t,2) for t in queued.ball_frames.values_list("time_seconds",flat=True))
                # Rebuild only unreviewed suggested timestamps; never modify verified annotations.
                BallFrameSuggestion.objects.filter(game=queued).delete()
                BallFrameSuggestion.objects.bulk_create([
                    BallFrameSuggestion(game=queued,time_seconds=t,reason=reason,score=score)
                    for t,reason,score in source_times if round(t,2) not in existing
                ],ignore_conflicts=True)
                Game.objects.filter(pk=queued.pk).update(
                    ball_sampling_state="ready",ball_sampling_error="")
                self.stdout.write(f"Game {queued.pk}: {queued.ball_suggestions.count()} unverified annotation suggestions")
            except Exception as exc:
                Game.objects.filter(pk=queued.pk).update(
                    ball_sampling_state="failed",ball_sampling_error=str(exc)[:600])
                self.stderr.write(f"Game {queued.pk} annotation sampling failed: {exc}")
            return True
        return False
