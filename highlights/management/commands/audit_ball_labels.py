import json
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from highlights.models import Game
from highlights.vision.data_quality import audit_games


class Command(BaseCommand):
    help="Read-only count audit of owner-approved basketball boxes and explicit negatives"

    def add_arguments(self, parser):
        parser.add_argument("--owner-id",type=int,required=True)

    def handle(self,*args,**opts):
        if not get_user_model().objects.filter(pk=opts["owner_id"]).exists():
            raise CommandError("Unknown data owner")
        games=list(Game.objects.filter(
            venue__owner_id=opts["owner_id"],ball_training_approved=True
        ).annotate(
            positive=Count("ball_frames",filter=Q(ball_frames__ball_absent=False)),
            negative=Count("ball_frames",filter=Q(ball_frames__ball_absent=True)),
            verified=Count("ball_frames",filter=Q(ball_frames__quality_verified=True)),
        ).order_by("pk").values("id","positive","negative"))
        # Model quality report uses deterministic game split; maintain sample-only
        # audit fallback but do not claim a valid split before export.
        report={
            "approved_game_count":len(games),
            "positive_frames":sum(row["positive"] for row in games),
            "explicit_negative_frames":sum(row["negative"] for row in games),
            "by_game":games,
            "verified_frames":sum(row["verified"] for row in games),
            "unverified_frames":sum(row["positive"]+row["negative"]-row["verified"] for row in games),
            "all_approved_labels_secondary_verified":all(
                row["verified"]==row["positive"]+row["negative"] for row in games),
            "meets_coverage_minimum_before_split":(
                len(games)>=4 and sum(x["positive"] for x in games)>=120
                and sum(x["negative"] for x in games)>=40),
            "warning":"Counts alone do NOT establish split validity, model accuracy or commercial licensing.",
        }
        self.stdout.write(json.dumps(report,ensure_ascii=False,indent=2))
