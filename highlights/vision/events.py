"""Pure temporal logic; fully testable without a GPU or OpenCV."""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BallObservation:
    time: float
    x: float  # normalized horizontal center
    y: float  # normalized vertical center
    confidence: float


def validate_hoops(hoops):
    if not isinstance(hoops, list) or not 1 <= len(hoops) <= 2:
        raise ValueError("请标注 1 或 2 个篮筐区域。")
    checked = []
    for region in hoops:
        if not isinstance(region, dict):
            raise ValueError("篮筐区域必须是 JSON 对象。")
        rect = {}
        for key in ("x", "y", "w", "h"):
            try:
                number = float(region[key])
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f"篮筐 {key} 坐标无效。") from exc
            if not math.isfinite(number):
                raise ValueError("篮筐坐标不能是 NaN 或 Infinity。")
            rect[key] = round(number, 5)
        if not (0 <= rect["x"] < 1 and 0 <= rect["y"] < 1
                and .01 <= rect["w"] <= .7 and .01 <= rect["h"] <= .7
                and rect["x"] + rect["w"] <= 1.00001
                and rect["y"] + rect["h"] <= 1.00001):
            raise ValueError("篮筐归一化区域必须处于视频画面内。")
        checked.append(rect)
    return checked


def _near_hoop(observation, rectangle):
    # Tolerant region: a basketball approaching the hoop is often wider than the rim ROI.
    padx = max(.035, rectangle["w"] * 1.5)
    pady = max(.04, rectangle["h"] * 1.6)
    return (rectangle["x"] - padx <= observation.x <= rectangle["x"] + rectangle["w"] + padx
            and rectangle["y"] - pady <= observation.y <= rectangle["y"] + rectangle["h"] + pady)


def propose_shots(observations, hoops, duration, *, min_hits=2, max_candidates=36):
    """Group nearby ball observations around a manually labeled hoop.
    
    A candidate is NOT proof of an attempted or made shot. Confidence is the
    *ball detector* confidence only, not a likelihood of scoring.
    """
    checked = validate_hoops(hoops)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("视频时长必须为正数。")
    if not 1 <= min_hits <= 10 or not 1 <= max_candidates <= 100:
        raise ValueError("候选筛选参数不正确。")
    proposals = []
    for index, hoop in enumerate(checked):
        hits = sorted((p for p in observations
                       if (isinstance(p, BallObservation) and
                           all(math.isfinite(v) for v in (p.time, p.x, p.y, p.confidence))
                           and 0 <= p.time <= duration and 0 <= p.confidence <= 1
                           and _near_hoop(p, hoop))),
                      key=lambda p: p.time)
        groups = []
        for point in hits:
            if not groups or point.time - groups[-1][-1].time > 3:
                groups.append([point])
            else:
                groups[-1].append(point)
        for group in groups:
            distinct_times = {round(p.time, 2) for p in group}
            if len(distinct_times) < min_hits:
                continue
            first, last = group[0].time, group[-1].time
            start, end = max(0, first - 5), min(duration, last + 7)
            # Reject overly long time intervals instead of producing ambiguous 2-minute segments.
            if end - start > 45:
                continue
            proposals.append({
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "source": "model_proposal",
                "score": round(max(p.confidence for p in group), 4),
                "note": f"篮筐{index + 1}附近发现篮球（{len(distinct_times)}帧）；非得分结论",
            })
    # Separate hoops can detect the same scene; dedupe heavily overlapping time intervals.
    proposals.sort(key=lambda p: (-p["score"], p["start_seconds"]))
    kept = []
    for candidate in proposals:
        if any(min(candidate["end_seconds"], other["end_seconds"]) -
               max(candidate["start_seconds"], other["start_seconds"]) >
               .7 * min(candidate["end_seconds"]-candidate["start_seconds"],
                         other["end_seconds"]-other["start_seconds"]) for other in kept):
            continue
        kept.append(candidate)
        if len(kept) == max_candidates:
            break
    return sorted(kept, key=lambda p: p["start_seconds"])
