"""Owner-only, never-auto-approved import of V0.6 research CPU observations."""
import json
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from .models import Candidate, Game
from .vision.bridge_v06 import media_quick_fingerprint, propose_research_review


@login_required
@require_POST
def import_research_observations(request, pk):
    game=get_object_or_404(Game.objects.select_related("venue"),pk=pk,
                           venue__owner=request.user)
    uploaded=request.FILES.get("observations")
    if not uploaded or uploaded.size <= 0 or uploaded.size > 180_000:
        messages.error(request,"请选择不超过 180KB 的 CPU 研究模型观测 JSON")
        return redirect("game",pk=pk)
    try:
        if not game.hoops:
            raise ValueError("请先在比赛详情标定 1~2 个篮筐")
        # Decode precisely one bounded model output; no arbitrary file paths from JSON.
        data=json.loads(uploaded.read().decode("utf-8-sig"))
        candidates=propose_research_review(data, game_id=game.pk,
            duration=game.duration_seconds, hoops=game.hoops,
            video_fingerprint=media_quick_fingerprint(game.video.path))
        if not candidates:
            messages.warning(request,"已检查 CPU 研究观测，未形成满足连续运动约束的候选；不自动伪造高光。")
            return redirect("game",pk=pk)
        existing={(source,round(start,3),round(end,3)) for source,start,end
                  in game.candidates.values_list("source","start_seconds","end_seconds")}
        batch=[]
        for candidate in candidates:
            key=(candidate["source"],candidate["start_seconds"],candidate["end_seconds"])
            if key in existing:
                continue
            existing.add(key)
            batch.append(Candidate(game=game,
                start_seconds=candidate["start_seconds"],
                end_seconds=candidate["end_seconds"],
                source="model_proposal", energy_score=candidate["score"],
                note=candidate["note"][:240],review="pending"))
        with transaction.atomic():
            Candidate.objects.bulk_create(batch)
        messages.success(request, f"V0.6 CPU 研究候选新导入 {len(batch)} 段，全部待人工审核。")
    except (ValueError, KeyError, TypeError, OSError, UnicodeDecodeError) as exc:
        messages.error(request,f"V0.6 观测导入失败：{exc}")
    return redirect("game",pk=pk)
