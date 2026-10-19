import json
import math
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Candidate, Export, Game, Venue, ShotAnnotation
from .services import MediaError, probe_duration
from .vision.events import validate_hoops


def _game(request, pk):
    return get_object_or_404(Game.objects.select_related("venue"), pk=pk, venue__owner=request.user)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("请输入有效的数字秒数。")


def _times(game, start_raw, end_raw):
    import math
    start = _float(start_raw)
    end = _float(end_raw)
    if not (math.isfinite(start) and math.isfinite(end) and
            0 <= start < end <= game.duration_seconds + .01 and end - start <= 120):
        raise ValueError("时间范围必须位于录像内，且单条不超过 120 秒。")
    return round(start, 3), round(end, 3)


@login_required
@require_GET
def dashboard(request):
    venues = Venue.objects.filter(owner=request.user).order_by("pk")
    games = Game.objects.filter(venue__owner=request.user).select_related("venue").order_by("-created_at")[:60]
    return render(request, "dashboard.html", {"venues": venues, "games": games})


@login_required
@require_POST
def create_venue(request):
    name = request.POST.get("name", "").strip()
    if not name or len(name) > 120:
        messages.error(request, "球馆名称必填，最多 120 字。")
    else:
        Venue.objects.create(owner=request.user, name=name)
        messages.success(request, "球馆创建成功。")
    return redirect("dashboard")


@login_required
@require_POST
def upload_game(request):
    venue = get_object_or_404(Venue, pk=request.POST.get("venue_id"), owner=request.user)
    title = request.POST.get("title", "").strip()
    incoming = request.FILES.get("video")
    if not title or len(title) > 180 or not isinstance(incoming, UploadedFile):
        messages.error(request, "请填写比赛名称并选择视频文件。")
        return redirect("dashboard")
    if Path(incoming.name).suffix.lower() not in {".mp4", ".mov", ".m4v", ".mkv", ".webm"}:
        messages.error(request, "仅支持 MP4、MOV、M4V、MKV 和 WEBM。")
        return redirect("dashboard")
    if incoming.size > settings.MAX_UPLOAD_BYTES or incoming.size == 0:
        messages.error(request, "文件不能为空或超过当前配置的上传限制。")
        return redirect("dashboard")
    game = Game(venue=venue, title=title, video=incoming)
    game.save()
    try:
        game.duration_seconds = probe_duration(game.video.path)
        game.save(update_fields=["duration_seconds"])
    except (MediaError, OSError, TimeoutError) as exc:
        game.video.delete(save=False)
        game.delete()
        messages.error(request, str(exc))
        return redirect("dashboard")
    messages.success(request, "视频上传完成，可开始自动候选分析。")
    return redirect("game", pk=game.pk)


@login_required
@require_GET
def game_detail(request, pk):
    game = _game(request, pk)
    candidates = game.candidates.all()
    annotations = game.shot_annotations.all()
    exports = game.exports.order_by("-created_at")[:20]
    return render(request, "game.html", {"game": game, "candidates": candidates, "exports": exports, "annotations": annotations})


@login_required
@require_POST
def queue_analysis(request, pk):
    game = _game(request, pk)
    mode = request.POST.get("engine", "audio")
    if mode not in ("audio", "cpu"):
        messages.error(request, "未知分析引擎。")
        return redirect("game", pk=pk)
    if game.state not in ("uploaded", "ready", "failed"):
        messages.error(request, "已有分析任务运行或排队中。")
    else:
        game.state = "queued"
        game.analysis_engine = mode
        game.error = ""
        game.save(update_fields=["state", "analysis_engine", "error"])
        messages.success(request, ("CPU视觉" if mode == "cpu" else "声音") + "候选任务已加入后台队列。")
    return redirect("game", pk=pk)


@login_required
@require_POST
def add_candidate(request, pk):
    game = _game(request, pk)
    try:
        start, end = _times(game, request.POST.get("start"), request.POST.get("end"))
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        Candidate.objects.create(
            game=game, start_seconds=start, end_seconds=end, source="manual",
            review="pending", player_label=request.POST.get("player_label", "").strip()[:100],
        )
        messages.success(request, "已添加手动候选片段，仍需审核通过。")
    return redirect("game", pk=pk)


@login_required
@require_POST
def review_candidate(request, pk, candidate_id):
    game = _game(request, pk)
    clip = get_object_or_404(Candidate, pk=candidate_id, game=game)
    review = request.POST.get("review")
    if review not in Candidate.Review.values:
        messages.error(request, "审核状态不合法。")
        return redirect("game", pk=pk)
    try:
        start, end = _times(game, request.POST.get("start"), request.POST.get("end"))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("game", pk=pk)
    clip.start_seconds, clip.end_seconds = start, end
    clip.player_label = request.POST.get("player_label", "").strip()[:100]
    clip.note = request.POST.get("note", "").strip()[:240]
    clip.review = review
    clip.save(update_fields=["start_seconds", "end_seconds", "player_label", "note", "review"])
    messages.success(request, "片段审核已保存。")
    return redirect("game", pk=pk)


@login_required
@require_POST
def queue_export(request, pk):
    game = _game(request, pk)
    label = request.POST.get("player_label", "").strip()[:100]
    qs = game.candidates.filter(review="approved")
    if label:
        qs = qs.filter(player_label=label)
    if not qs.exists():
        messages.error(request, "找不到匹配的已通过片段，请先审核。")
    else:
        Export.objects.create(game=game, player_label=label)
        messages.success(request, "渲染任务已排队，完成后将出现在下载列表。")
    return redirect("game", pk=pk)


@login_required
@require_POST
def save_hoops(request, pk):
    game = _game(request, pk)
    try:
        raw = request.POST.get("hoops", "")
        if len(raw) > 3000:
            raise ValueError("篮筐区域数据过长。")
        regions = json.loads(raw)
        hoops = [] if regions == [] else validate_hoops(regions)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        messages.error(request, f"篮筐标定无效：{exc}")
    else:
        game.hoops = hoops
        game.save(update_fields=["hoops"])
        messages.success(request, f"已保存 {len(hoops)} 个篮筐区域。")
    return redirect("game", pk=pk)


@login_required
@require_GET
def vision_config(request, pk):
    game = _game(request, pk)
    if not game.hoops:
        return HttpResponse("请先标定篮筐区域。", status=400, content_type="text/plain; charset=utf-8")
    response = JsonResponse({
        "schema_version": 2,
        "game_id": game.pk,
        "video_duration_seconds": round(game.duration_seconds, 3),
        "hoops": validate_hoops(game.hoops),
    }, json_dumps_params={"ensure_ascii": False})
    response["Content-Disposition"] = f'attachment; filename="game-{game.pk}-hoops.json"'
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def import_proposals(request, pk):
    """Import GPU *proposals*, not confirmed scores; every new clip remains pending."""
    game = _game(request, pk)
    incoming = request.FILES.get("proposals")
    if not incoming or incoming.size > 200_000 or incoming.size <= 0:
        messages.error(request, "请选择小于 200KB 的候选 JSON 文件。")
        return redirect("game", pk=pk)
    try:
        payload = json.loads(incoming.read().decode("utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version", 1) not in (1, 2):
            raise ValueError("不支持的候选 JSON schema_version。")
        if "game_id" in payload and str(payload["game_id"]) != str(game.pk):
            raise ValueError("此 JSON 属于另一场比赛。")
        if "video_duration_seconds" in payload:
            try:
                candidate_duration = float(payload["video_duration_seconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError("视频时长无效。") from exc
            if not math.isfinite(candidate_duration) or abs(candidate_duration - game.duration_seconds) > 3:
                raise ValueError("候选视频时长与该比赛不匹配。")
        items = payload["candidates"]
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError("JSON candidates 必须包含 1 到 100 个片段。")
        new_clips = []
        existing = {
            (src, round(start, 3), round(end, 3))
            for src, start, end in Candidate.objects.filter(game=game).values_list(
                "source", "start_seconds", "end_seconds")
        }
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("候选片段格式不正确。")
            start, end = _times(game, item.get("start_seconds"), item.get("end_seconds"))
            source = item.get("source", "visual_ball_presence")
            if source not in ("visual_ball_presence", "model_proposal"):
                raise ValueError("不支持该候选来源类型。")
            score = float(item.get("score", 0))
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("score 必须处于 0 到 1 之间。")
            key = (source, start, end)
            if key in existing:
                continue
            existing.add(key)
            new_clips.append(Candidate(
                game=game, start_seconds=start, end_seconds=end, source=source,
                energy_score=score, note=str(item.get("note", ""))[:240],
                review="pending",
            ))
        with transaction.atomic():
            Candidate.objects.bulk_create(new_clips)
        messages.success(request, f"已导入 {len(new_clips)} 条待审视觉候选。")
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        messages.error(request, f"候选 JSON 无效：{exc}")
    return redirect("game", pk=pk)


@login_required
@require_POST
def add_shot_annotation(request, pk):
    """Ground truth is a human-labeling operation, entirely separate from proposals."""
    game = _game(request, pk)
    try:
        time_seconds = _float(request.POST.get("time_seconds"))
        if not (math.isfinite(time_seconds) and
                0 <= time_seconds <= game.duration_seconds):
            raise ValueError("投篮时间必须位于视频范围内。")
        if game.shot_annotations.count() >= 3000:
            raise ValueError("一场比赛最多支持 3000 个投篮标注。")
        if game.shot_annotations.filter(
                time_seconds__gte=time_seconds - 0.4,
                time_seconds__lte=time_seconds + 0.4).exists():
            raise ValueError("该时刻附近已有投篮标注。")
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        with transaction.atomic():
            ShotAnnotation.objects.create(game=game, time_seconds=round(time_seconds, 3))
            Game.objects.filter(pk=game.pk).update(annotations_complete=False)
        messages.success(request, "已记录一条人工投篮尝试标注（不是进球命中）。")
    return redirect("game", pk=pk)


@login_required
@require_POST
def delete_shot_annotation(request, pk, annotation_id):
    game = _game(request, pk)
    annotation = get_object_or_404(ShotAnnotation, game=game, pk=annotation_id)
    with transaction.atomic():
        annotation.delete()
        Game.objects.filter(pk=game.pk).update(annotations_complete=False)
    messages.success(request, "投篮标注已删除，请重新确认标注完整性。")
    return redirect("game", pk=pk)


@login_required
@require_POST
def confirm_ground_truth(request, pk):
    game = _game(request, pk)
    # Explicit human attestation; any future edit resets this flag.
    complete = request.POST.get("complete") == "yes"
    game.annotations_complete = complete
    game.save(update_fields=["annotations_complete"])
    if complete:
        messages.success(request, "已标记人工投篮尝试标注完整，可导出评估基准。")
    else:
        messages.success(request, "已撤销标注完整确认。")
    return redirect("game", pk=pk)


@login_required
@require_GET
def download_ground_truth(request, pk):
    game = _game(request, pk)
    if not game.annotations_complete:
        return HttpResponse(
            "必须先完整检查比赛，并在页面确认所有投篮尝试已标注。",
            status=409, content_type="text/plain; charset=utf-8")
    payload = {
        "schema_version": 1,
        "game_id": game.pk,
        "video_duration_seconds": round(game.duration_seconds, 3),
        "complete_within_scope": True,
        "events": [{"type": "shot_attempt", "time_seconds": item.time_seconds}
                   for item in game.shot_annotations.all()],
    }
    response = JsonResponse(payload, json_dumps_params={"ensure_ascii": False})
    response["Content-Disposition"] = f'attachment; filename="game-{game.pk}-truth.json"'
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def delete_game(request, pk):
    game = _game(request, pk)
    # Explicitly remove files: FileField doesn't remove storage objects on cascade.
    for export in game.exports.all():
        if export.video:
            export.video.delete(save=False)
    if game.video:
        game.video.delete(save=False)
    game.delete()
    messages.success(request, "比赛及其本地录像、导出文件已删除。")
    return redirect("dashboard")


def _chunks(path, start, length):
    with open(path, "rb") as stream:
        stream.seek(start)
        remaining = length
        while remaining > 0:
            data = stream.read(min(1024 * 512, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _private_media(request, field, download=False):
    if not field:
        raise Http404
    path = field.path
    if not Path(path).is_file():
        raise Http404
    size = Path(path).stat().st_size
    if download:
        response = FileResponse(open(path, "rb"), as_attachment=True,
                                filename=Path(path).name, content_type="video/mp4")
        response["Cache-Control"] = "private, no-store"
        return response
    match = re.fullmatch(r"bytes=(\d+)-(\d*)", request.headers.get("Range", ""))
    if match:
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else size - 1
        if start >= size or end < start:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        end = min(end, size - 1)
        response = StreamingHttpResponse(_chunks(path, start, end - start + 1),
                                         status=206, content_type="video/mp4")
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    else:
        response = StreamingHttpResponse(_chunks(path, 0, size), content_type="video/mp4")
        response["Content-Length"] = str(size)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def source_video(request, pk):
    return _private_media(request, _game(request, pk).video)


@login_required
@require_GET
def export_video(request, pk, export_id):
    game = _game(request, pk)
    export = get_object_or_404(Export, pk=export_id, game=game, state="done")
    return _private_media(request, export.video, download=True)


# V0.3 independent owner-only video frame labeling UI
from .ball_views import (ball_labels, ball_frame_jpg, save_ball_label,
                         delete_ball_label, approve_training_data, queue_ball_sampling,
                         verify_ball_label, revoke_ball_verification)

# V0.6 owner-only research observation import (all results require manual review)
from .v06_views import import_research_observations

# V0.8 owner-scoped campaign board + data readiness report
from .campaign_views import ball_campaign, ball_campaign_json
