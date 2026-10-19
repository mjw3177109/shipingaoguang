"""V0.6 research-only observations -> conservative pending review suggestions.

No shot/made-basket classification, no automatic approval or export.
An identical VIDEO-FILE light fingerprint is required to prevent accidental
cross-game imports. It is not a full-file cryptographic identity check.
"""
import hashlib
import math
from pathlib import Path

from .events import BallObservation, validate_hoops, _near_hoop


def media_quick_fingerprint(path):
    """SHA-256 over file length and first/last 64KiB (not full-file proof)."""
    source=Path(path)
    size=source.stat().st_size
    if size <= 0:
        raise ValueError("Source video is empty")
    digest=hashlib.sha256()
    digest.update(f"basketcut-v06-quick:{size}:".encode())
    with source.open("rb") as file:
        digest.update(file.read(65536))
        if size > 65536:
            file.seek(max(0,size-65536))
            digest.update(file.read(65536))
    return digest.hexdigest()


def _finite_numeric(value, name):
    if isinstance(value, bool) or not isinstance(value,(int,float)):
        raise ValueError(f"{name}: expected finite numeric value")
    if not math.isfinite(value):
        raise ValueError(f"{name}: nonfinite value")
    return float(value)


def validated_observations(payload, *, game_id, duration, hoops, video_fingerprint):
    if not isinstance(payload,dict) or payload.get("schema_version") != 2:
        raise ValueError("Expected model schema_version=2 JSON")
    if type(payload.get("game_id")) is not int or payload["game_id"] != game_id:
        raise ValueError("Mismatched or missing game ID")
    provided_duration=_finite_numeric(payload.get("video_duration_seconds"),"duration")
    if not .01<=duration<=21600 or abs(provided_duration-duration)>0.5:
        raise ValueError("This observation file belongs to a different video duration")
    checked=validate_hoops(hoops)
    included=validate_hoops(payload.get("hoops"))
    if checked!=included:
        raise ValueError("Hoop calibration differs from this game")
    if (not isinstance(video_fingerprint,str) or len(video_fingerprint)!=64 or
            payload.get("video_quick_fingerprint_sha256")!=video_fingerprint):
        raise ValueError("Video fingerprint mismatch; re-run on this exact uploaded video")
    if payload.get("research_only") is not True or payload.get("review_only") is not True:
        raise ValueError("Only explicitly research-only, human-review-only observations are accepted")
    analysis=payload.get("analysis")
    if (not isinstance(analysis,dict) or analysis.get("device")!="cpu"
            or analysis.get("checkpoint_used") is not True
            or analysis.get("model")!="basketball_specialist_mobile320_v1"):
        raise ValueError("Expected actual CPU basketball specialist observations")
    if analysis.get("incomplete_scan") is not False:
        raise ValueError("Partial video scans must not be imported as full-match results")
    if payload.get("observations_truncated") is not False:
        raise ValueError("Truncated observation data cannot be imported")
    coverage=_finite_numeric(analysis.get("analyzed_until_seconds"),"coverage")
    if coverage < duration-.2:
        raise ValueError("Video was not scanned to the end")
    raw=payload.get("observations")
    if not isinstance(raw,list) or len(raw)>1500:
        raise ValueError("Too many observations")
    results=[]
    for item in raw:
        if not isinstance(item,dict):
            raise ValueError("Malformed frame observation")
        t=_finite_numeric(item.get("time"),"time")
        x=_finite_numeric(item.get("x"),"x")
        y=_finite_numeric(item.get("y"),"y")
        confidence=_finite_numeric(item.get("confidence"),"confidence")
        if not (0<=t<duration+.01 and 0<=x<=1 and 0<=y<=1 and 0<=confidence<=1):
            raise ValueError("Observation outside video or normalized frame")
        results.append(BallObservation(t,x,y,confidence))
    return sorted(results,key=lambda row:(row.time,row.x,row.y)),checked


def propose_research_review(payload, *, game_id, duration, hoops, video_fingerprint,
                            max_clips=16, min_detector_score=.10):
    """Track plausibly continuous moving small objects near a confirmed hoop.

    More conservative than general sports-ball presence; always pending review.
    """
    if not 1<=max_clips<=50 or not 0<=min_detector_score<=1:
        raise ValueError("Invalid V0.6 bounds")
    observations,checked=validated_observations(payload,game_id=game_id,
        duration=duration,hoops=hoops,video_fingerprint=video_fingerprint)
    candidates=[]
    for index,hoop in enumerate(checked):
        events=[p for p in observations if p.confidence>=min_detector_score and _near_hoop(p,hoop)]
        tracks=[]
        for p in events:
            nearest=None
            distance_limit=.15
            for track in tracks:
                prev=track[-1]
                dt=p.time-prev.time
                distance=math.hypot(p.x-prev.x,p.y-prev.y)
                if .09<=dt<=1.6 and distance<distance_limit:
                    nearest=track
                    distance_limit=distance
            if nearest is None:
                tracks.append([p])
            else:
                nearest.append(p)
        for track in tracks:
            unique={round(item.time,2) for item in track}
            span=track[-1].time-track[0].time
            path=sum(math.hypot(a.x-b.x,a.y-b.y) for a,b in zip(track,track[1:]))
            if len(unique)<3 or not .35<=span<=6 or path<.025:
                continue
            start=max(0,track[0].time-4)
            end=min(duration,track[-1].time+5)
            if not 1<=end-start<=18:
                continue
            candidates.append({
                "start_seconds":round(start,3),
                "end_seconds":round(end,3),
                "score":round(max(p.confidence for p in track),4),
                "source":"model_proposal",
                "note":f"V0.6研究模型：篮筐{index+1}附近运动球状目标，{len(unique)}时刻；非投篮/命中结论，待人工审核",
            })
    candidates.sort(key=lambda r:(-r["score"],r["start_seconds"]))
    selected=[]
    for row in candidates:
        if any(min(row["end_seconds"],prev["end_seconds"])-
               max(row["start_seconds"],prev["start_seconds"]) >=
               .65*min(row["end_seconds"]-row["start_seconds"],
                       prev["end_seconds"]-prev["start_seconds"]) for prev in selected):
            continue
        selected.append(row)
        if len(selected)==max_clips:
            break
    return sorted(selected,key=lambda row:row["start_seconds"])
