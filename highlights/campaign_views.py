"""Authenticated multi-game campaign board; no raw media/PII served in JSON."""
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import Game
from .vision.campaign_v08 import build_campaign_report


def owner_campaign_data(owner):
    queryset=(Game.objects.filter(venue__owner=owner).select_related("venue")
        .annotate(
            positive_n=Count("ball_frames",filter=Q(ball_frames__ball_absent=False),distinct=True),
            negative_n=Count("ball_frames",filter=Q(ball_frames__ball_absent=True),distinct=True),
            verified_n=Count("ball_frames",filter=Q(
                ball_frames__quality_verified=True,
                ball_frames__quality_verified_at__isnull=False,
                ball_frames__quality_verified_by__isnull=False),distinct=True),
            pending_n=Count("ball_suggestions",distinct=True),
        ).order_by("pk"))
    rows=[{
        "id":g.pk,"title":g.title,"venue_name":g.venue.name,
        "positive":g.positive_n,"negative":g.negative_n,
        "verified":g.verified_n,"pending":g.pending_n,
        "approved":g.ball_training_approved,
        "sampling_state":g.ball_sampling_state,
        "sampling_rounds":g.ball_sampling_round,
    } for g in queryset]
    return build_campaign_report(rows)


@login_required
@require_GET
def ball_campaign(request):
    return render(request,"ball_campaign.html",
                  {"report":owner_campaign_data(request.user)})


@login_required
@require_GET
def ball_campaign_json(request):
    response=JsonResponse(owner_campaign_data(request.user),
                          json_dumps_params={"ensure_ascii":False})
    response["Cache-Control"]="private, no-store"
    response["X-Content-Type-Options"]="nosniff"
    return response
