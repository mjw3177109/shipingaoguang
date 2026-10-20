"""Owner-only basketball frame labeling; training is explicitly opt-in."""
import json
import math
import subprocess

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .ball_labels import validate_ball_label
from .models import BallFrameAnnotation, BallFrameSuggestion, Game


def _owner_game(request, pk):
    return get_object_or_404(Game.objects.select_related("venue"),
                             pk=pk, venue__owner=request.user)


def extract_frame_jpeg(game, t, *, width=960):
    """Frame is served from authenticated original video only, never from public storage."""
    sec = float(t)
    if not math.isfinite(sec) or not 0 <= sec < game.duration_seconds:
        raise ValueError("无效的帧时间")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{sec:.3f}", "-i", game.video.path, "-frames:v", "1",
        "-vf", f"scale={width}:-2", "-q:v", "3", "-f", "image2pipe",
        "-vcodec", "mjpeg", "pipe:1",
    ]
    result = subprocess.run(command, capture_output=True, timeout=35, check=False)
    if result.returncode or not result.stdout or len(result.stdout) > 8_000_000:
        raise ValueError("无法读取该时间位置的画面")
    return result.stdout


@login_required
@require_GET
def ball_labels(request, pk):
    game = _owner_game(request, pk)
    page = Paginator(game.ball_frames.order_by("time_seconds"), 50).get_page(request.GET.get("page"))
    rows = page.object_list
    total = game.ball_frames.count()
    reviewed = game.ball_frames.filter(quality_verified=True).count()
    suggestions = game.ball_suggestions.order_by("time_seconds")[:50]
    gallery_suggestions = game.ball_suggestions.order_by("-score","time_seconds")[:6]
    return render(request, "ball_labels.html", {"game":game, "labels":rows,
                                                "suggestions":suggestions,
                                                "gallery_suggestions":gallery_suggestions,
                                                "page_obj":page,
                                                "verified_count":reviewed,
                                                "annotation_count":total,
                                                "unverified_count":total-reviewed})


@login_required
@require_GET
def ball_frame_jpg(request, pk):
    game = _owner_game(request, pk)
    try:
        output = extract_frame_jpeg(game, request.GET.get("t", "0"))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return HttpResponse("无法读取视频帧", status=400, content_type="text/plain; charset=utf-8")
    response = HttpResponse(output, content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def save_ball_label(request, pk):
    game = _owner_game(request, pk)
    absent = request.POST.get("ball_absent") == "yes"
    try:
        moment, (x,y,w,h) = validate_ball_label(
            request.POST.get("time_seconds"),
            None if absent else {a:request.POST.get(a) for a in ("x","y","w","h")},
            game.duration_seconds, absent=absent)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ball_labels", pk=pk)
    allowed_reasons = {"", "jersey", "court", "hoop", "advertisement", "crowd", "occlusion", "other"}
    negative_reason = request.POST.get("hard_negative_reason", "").strip() if absent else ""
    if negative_reason not in allowed_reasons:
        messages.error(request, "不支持的干扰类别")
        return redirect("ball_labels", pk=pk)
    with transaction.atomic():
        # Disallow unbounded annotation inflation.
        if not BallFrameAnnotation.objects.filter(game=game,time_seconds=moment).exists() and game.ball_frames.count() >= 3000:
            messages.error(request, "每场最多 3000 帧标注")
            return redirect("ball_labels", pk=pk)
        BallFrameAnnotation.objects.update_or_create(
            game=game,time_seconds=moment,
            defaults={"ball_absent":absent,"hard_negative_reason":negative_reason,
                      "x":x,"y":y,"w":w,"h":h,
                      "quality_verified":False,
                      "quality_verified_at":None,
                      "quality_verified_by":None})
        BallFrameSuggestion.objects.filter(game=game,time_seconds__gte=moment-.02,
                                           time_seconds__lte=moment+.02).delete()
        Game.objects.filter(pk=pk).update(ball_training_approved=False)
    messages.success(request, f"已保存 {moment:.2f}s 球框标注。二次复核及训练许可需要重新确认。")
    if request.POST.get("save_next") == "yes":
        remaining = list(game.ball_suggestions.order_by("time_seconds").values_list("time_seconds", flat=True)[:50])
        if remaining:
            next_time=next((v for v in remaining if v > moment+.03), remaining[0])
            return redirect(f"{reverse('ball_labels', kwargs={'pk':pk})}?t={next_time:.2f}")
    return redirect("ball_labels",pk=pk)


@login_required
@require_POST
def delete_ball_label(request, pk, label_id):
    game = _owner_game(request, pk)
    label = get_object_or_404(BallFrameAnnotation,pk=label_id,game=game)
    with transaction.atomic():
        label.delete()
        Game.objects.filter(pk=pk).update(ball_training_approved=False)
    messages.success(request, "球框标注已删除，训练许可确认已撤销。")
    return redirect("ball_labels",pk=pk)


@login_required
@require_POST
def approve_training_data(request, pk):
    game = _owner_game(request, pk)
    text = request.POST.get("rights_note", "").strip()
    approve = request.POST.get("approve") == "yes"
    if approve and (not 12 <= len(text) <= 240 or not game.ball_frames.exists()):
        messages.error(request, "需填写至少 12 字的视频授权或权利依据，并先完成至少一个标注。")
        return redirect("ball_labels",pk=pk)
    game.ball_training_approved = approve
    game.ball_training_rights = text[:240]
    game.save(update_fields=["ball_training_approved","ball_training_rights"])
    messages.success(request, "训练数据授权确认已更新。")
    return redirect("ball_labels",pk=pk)


@login_required
@require_POST
def queue_ball_sampling(request, pk):
    game = _owner_game(request,pk)
    # Do not block Django HTTP workers on ffmpeg/OpenCV. Existing CPU worker runs it.
    changed = Game.objects.filter(pk=game.pk,ball_sampling_state__in=(
        "idle","ready","failed")).update(
            ball_sampling_state="queued",ball_sampling_error="",
            ball_sampling_started_at=None, ball_sampling_heartbeat_at=None,
            ball_sampling_worker_token="", ball_sampling_recovery_count=0,
            ball_sampling_round=F("ball_sampling_round")+1)
    if changed:
        messages.success(request,"CPU 智能抽帧已排队；标注建议不等于真实篮球标签。")
    else:
        messages.info(request,"此场比赛已经在等待或运行 CPU 抽帧。")
    return redirect("ball_labels",pk=pk)


@login_required
@require_POST
def verify_ball_label(request, pk, label_id):
    """Explicit second inspection required after a label is entered/edited."""
    game=_owner_game(request,pk)
    label=get_object_or_404(BallFrameAnnotation, pk=label_id, game=game)
    if request.POST.get("verified")!="yes":
        messages.error(request, "必须逐帧查看后，明确确认本帧标注已复核。")
        return redirect("ball_labels",pk=pk)
    # A positive must contain a valid bbox; absent must contain no stored bbox.
    try:
        validate_ball_label(
            label.time_seconds,
            None if label.ball_absent else {
                "x":label.x,"y":label.y,"w":label.w,"h":label.h},
            game.duration_seconds, absent=label.ball_absent)
    except ValueError as exc:
        messages.error(request,f"本帧无法通过数据复核：{exc}")
        return redirect("ball_labels",pk=pk)
    if not label.quality_verified:
        with transaction.atomic():
            label.quality_verified=True
            label.quality_verified_at=timezone.now()
            label.quality_verified_by=request.user
            label.save(update_fields=[
                "quality_verified","quality_verified_at","quality_verified_by"])
            Game.objects.filter(pk=game.pk).update(ball_training_approved=False)
    messages.success(request,f"{label.time_seconds:.2f}s 二次复核通过；仍需在训练数据授权区重新确认授权。")
    return redirect("ball_labels",pk=pk)


@login_required
@require_POST
def revoke_ball_verification(request, pk, label_id):
    game=_owner_game(request,pk)
    label=get_object_or_404(BallFrameAnnotation,pk=label_id,game=game)
    if label.quality_verified:
        with transaction.atomic():
            label.quality_verified=False
            label.quality_verified_at=None
            label.quality_verified_by=None
            label.save(update_fields=[
                "quality_verified","quality_verified_at","quality_verified_by"])
            Game.objects.filter(pk=game.pk).update(ball_training_approved=False)
    messages.success(request,"已撤销本帧复核；训练许可确认已撤销。")
    return redirect("ball_labels",pk=pk)
