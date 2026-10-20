"""V1.0 immutable human-reviewed export plan.

No detections are promoted here. Every frame segment must be explicitly
approved. The worker rejects any plan whose clips were edited/rejected after
queueing, rather than quietly rendering a different cut.
"""
import hashlib
import json
import math

from .vision.bridge_v06 import media_quick_fingerprint


class PlanError(ValueError):
    pass


def _digest(plan):
    return hashlib.sha256(json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()


def _validate_selection(game, items):
    if not items:
        raise PlanError("没有已通过的片段，请先人工审核。")
    if len(items) > 150:
        raise PlanError("一次最多导出 150 个片段。")
    output=[]
    total=0.
    for clip in items:
        start, end = float(clip.start_seconds), float(clip.end_seconds)
        if not (math.isfinite(start) and math.isfinite(end)
                and 0 <= start < end <= game.duration_seconds + .01
                and end-start <= 120):
            raise PlanError(f"候选片段 {clip.pk} 时间范围不合法。")
        total+=end-start
        output.append({
            "candidate_id": clip.pk,
            "start_seconds": round(start,3),
            "end_seconds": round(end,3),
            "player_label": clip.player_label,
            "source": clip.source,
        })
    if total>1800:
        raise PlanError("单个导出任务片段累计时长最多 30 分钟。")
    return output, round(total, 3)


def create_plan(game, player_label, *, audio_mode="silent"):
    if audio_mode not in ("silent", "original"):
        raise PlanError("音频模式只能是原声或无声。")
    selected=game.candidates.filter(review="approved")
    if player_label:
        selected=selected.filter(player_label=player_label)
    clips,total=_validate_selection(
        game,list(selected.order_by("start_seconds","pk")))
    plan={
        "schema_version":1,
        "game_id":game.pk,
        "video_quick_fingerprint_sha256":media_quick_fingerprint(game.video.path),
        "video_duration_seconds":round(game.duration_seconds,3),
        "player_label":player_label,
        "audio_mode": audio_mode,
        "clip_count":len(clips),
        "total_clipped_seconds":total,
        "clips":clips,
        "human_review_required":True,
        "automatic_scoring_claim":False,
        "render_profile":"720p_30fps_h264_aac" if audio_mode=="original"
                          else "720p_30fps_h264_silent",
    }
    return plan,_digest(plan)


def validate_plan(job):
    """Revalidate original snapshot before rendering, including approved edits."""
    plan=job.plan
    if not isinstance(plan,dict) or plan.get("schema_version")!=1:
        raise PlanError("缺少 V1.0 快照，不允许静默改变镜头。")
    if _digest(plan)!=job.plan_sha256:
        raise PlanError("导出计划校验失败（快照已改变）。")
    if plan.get("game_id")!=job.game_id or plan.get("player_label")!=job.player_label:
        raise PlanError("导出计划属于另一场比赛或球员。")
    if plan.get("audio_mode","silent")!=job.audio_mode:
        raise PlanError("音频模式与导出任务不一致。")
    expected_profile = ("720p_30fps_h264_aac" if job.audio_mode=="original"
                        else "720p_30fps_h264_silent")
    if job.audio_mode not in ("silent","original") or plan.get("render_profile")!=expected_profile:
        raise PlanError("未知的导出编码方案。")
    if plan.get("human_review_required") is not True or plan.get("automatic_scoring_claim") is not False:
        raise PlanError("导出计划缺少明确人工审核声明。")
    if plan.get("video_quick_fingerprint_sha256")!=media_quick_fingerprint(job.game.video.path):
        raise PlanError("原始录像已更换，拒绝继续渲染。")
    if abs(float(plan.get("video_duration_seconds",-1))-job.game.duration_seconds)>.01:
        raise PlanError("原始视频时长变更，请重新申请导出。")
    clips=plan.get("clips")
    if not isinstance(clips,list) or not 1<=len(clips)<=150:
        raise PlanError("导出快照片段数量不合法。")
    if plan.get("clip_count")!=len(clips):
        raise PlanError("快照数量不一致。")
    ids=[item.get("candidate_id") for item in clips if isinstance(item,dict)]
    if len(ids)!=len(clips) or len(set(ids))!=len(ids) or any(type(cid) is not int for cid in ids):
        raise PlanError("快照包含无效或重复的片段标识。")
    current={clip.pk:clip for clip in job.game.candidates.filter(pk__in=ids)}
    ordered=[]
    for original in clips:
        clip=current.get(original["candidate_id"])
        if clip is None or clip.review!="approved":
            raise PlanError("某片段已被删除或撤销审核，须重新建立导出任务。")
        if (round(clip.start_seconds,3)!=original.get("start_seconds")
                or round(clip.end_seconds,3)!=original.get("end_seconds")
                or clip.player_label!=original.get("player_label")
                or clip.source!=original.get("source")):
            raise PlanError("某片段已被修改，拒绝使用失效的导出快照。")
        if job.player_label and clip.player_label!=job.player_label:
            raise PlanError("候选球员与导出计划不一致。")
        ordered.append(clip)
    checked,total=_validate_selection(job.game, ordered)
    if checked!=clips or abs(total-float(plan.get("total_clipped_seconds",-1)))>.01:
        raise PlanError("片段快照内容或总时长不一致。")
    return ordered


def export_metadata(job):
    """Private EDL JSON for reproducibility without exposing disk paths."""
    if job.plan:
        return {
            "schema_version":1,
            "export_id":job.pk,
            "game_id":job.game_id,
            "state":job.state,
            "created_at":job.created_at.isoformat(),
            "retry_of_export_id":job.retry_of_id,
            "attempt":job.attempt,
            "plan_sha256":job.plan_sha256,
            "plan":job.plan,
            "warning":"Review-approved edits only; not verified made baskets.",
        }
    return {
        "schema_version":1,"export_id":job.pk,"game_id":job.game_id,
        "state":job.state,"legacy_export":True,"plan":None,
        "warning":"Legacy job without an immutable clip snapshot.",
    }
