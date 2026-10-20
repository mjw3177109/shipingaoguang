"""No-download CPU ball-activity baseline using OpenCV orange-object shape detection.

This is a *heuristic* (no learned weights), not an accurate shot/made-basket model.
It is intentionally suitable for the first legal/free, reproducible smoke tests.
"""
import argparse
import json
import math
import time
from pathlib import Path

from .events import BallObservation, propose_shots, validate_hoops


def _colored_ball_candidates(frame, cv2, np):
    """Yield normalized positions + heuristic quality; false positives are expected."""
    h, w = frame.shape[:2]
    scale = min(1.0, 640 / max(h, w))
    if scale < 1:
        frame = cv2.resize(frame, (round(w * scale), round(h * scale)))
    small_h, small_w = frame.shape[:2]
    hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([4, 90, 60], dtype=np.uint8),
                      np.array([23, 255, 255], dtype=np.uint8))
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    result = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 10 or area > small_w * small_h * .015:
            continue
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < 4 or bh < 4 or not .55 <= bw / bh <= 1.8:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * math.pi * area / (perimeter * perimeter) if perimeter else 0
        if circularity < .38:
            continue
        cx = (x + bw / 2) / small_w
        cy = (y + bh / 2) / small_h
        quality = min(.95, max(.05, circularity * min(1.0, area / 45)))
        result.append((cx, cy, quality))
    return sorted(result, key=lambda obj: obj[2], reverse=True)[:8]


def _activity_candidates(observations, duration, *, max_candidates=36):
    """No hoop ROI: suggest orange-ball activity, explicitly NOT shots."""
    hits = sorted(observations, key=lambda p: p.time)
    groups = []
    for point in hits:
        if not groups or point.time - groups[-1][-1].time > 1.0:
            groups.append([point])
        else:
            groups[-1].append(point)
    proposed = []
    for group in groups:
        if len({round(p.time, 2) for p in group}) < 2:
            continue
        # Long orange objects (jersey, court) should not create indefinite clips.
        for start_idx in range(0, len(group), 24):
            part = group[start_idx:start_idx+24]
            center = part[len(part)//2].time
            s, e = max(0, center - 3), min(duration, center + 4)
            if e - s < 1:
                continue
            proposed.append({
                "start_seconds": round(s, 3), "end_seconds": round(e, 3),
                "source": "cpu_ball_activity", "score": round(max(p.confidence for p in part), 4),
                "note": "CPU橙色球状物体活动候选；非得分结论，可能误检球衣",
            })
    proposed.sort(key=lambda c: (-c["score"], c["start_seconds"]))
    picked = []
    for item in proposed:
        if all(abs(item["start_seconds"]-p["start_seconds"]) >= 4 for p in picked):
            picked.append(item)
        if len(picked) >= max_candidates:
            break
    return sorted(picked, key=lambda x:x["start_seconds"])


def scan_cpu_video(path, *, hoops=None, sample_seconds=.35, max_frames=10000, heartbeat=None):
    import cv2
    import numpy as np
    if not .15 <= sample_seconds <= 5 or not 1 <= max_frames <= 20000:
        raise ValueError("sample_seconds must be .15..5 and max_frames 1..20000")
    checked = validate_hoops(hoops) if hoops else []
    video = cv2.VideoCapture(str(path))
    if not video.isOpened():
        raise ValueError("OpenCV cannot open video")
    started = time.monotonic()
    try:
        fps = float(video.get(cv2.CAP_PROP_FPS))
        frames = float(video.get(cv2.CAP_PROP_FRAME_COUNT))
        if not all(math.isfinite(v) for v in (fps, frames)) or fps <= 0 or frames < 1:
            raise ValueError("Unusable video FPS/frame-count")
        duration = frames / fps
        scheduled = min(int(math.ceil(duration / sample_seconds)), max_frames)
        observations = []
        decoded = 0
        last_time = 0
        prev_points = []
        for i in range(scheduled):
            if heartbeat:
                heartbeat()
            t = i * sample_seconds
            video.set(cv2.CAP_PROP_POS_MSEC, 1000*t)
            ok, frame = video.read()
            if not ok:
                break
            decoded += 1
            last_time = t
            locations = _colored_ball_candidates(frame, cv2, np)
            # Require appearance shift across successive samples; a static orange
            # advertisement/jersey can still create false positives and must be reviewed.
            current = []
            for x, y, q in locations:
                moved = bool(prev_points and all(
                    math.hypot(x-ox, y-oy) > .009 for ox, oy in prev_points))
                if moved:
                    observations.append(BallObservation(round(t,3), x, y, q))
                current.append((x,y))
            prev_points = current
        if decoded == 0:
            raise ValueError("Video has no decodable frames")
        analyzed_until = min(duration, last_time + sample_seconds)
        incomplete = analyzed_until < duration - .05
        if checked:
            clips = propose_shots(observations, checked, duration, min_hits=2)
            for clip in clips:
                clip["source"] = "cpu_hoop_activity"
                clip["note"] = "CPU颜色/圆度检测：篮筐附近活动候选；非进球识别，需审核"
        else:
            clips = _activity_candidates(observations, duration)
        return {
            "schema_version": 2, "video_duration_seconds": round(duration,3),
            "analysis": {
                "kind": "cpu_color_shape_motion_heuristic",
                "model": "opencv_hsv_contour_cpu_no_weights",
                "device": "cpu", "sample_interval_seconds": sample_seconds,
                "frames_processed": decoded, "observations": len(observations),
                "hoop_roi_provided": bool(checked),
                "incomplete_scan": incomplete,
                "analyzed_until_seconds": round(analyzed_until,3),
                "elapsed_seconds": round(time.monotonic()-started,3),
                "warning": "Only an untrained ball-like object heuristic. Not made-shot detection.",
            },
            "hoops": checked,
            "candidates": clips,
        }
    finally:
        video.release()


def main(argv=None):
    p = argparse.ArgumentParser(description="No-cost CPU basketball candidate experiment")
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--config", help="Optional game-specific hoop ROI JSON")
    p.add_argument("--sample-seconds", type=float, default=.35)
    p.add_argument("--max-frames", type=int, default=10000)
    args = p.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
    output = scan_cpu_video(args.video, hoops=config.get("hoops"),
                            sample_seconds=args.sample_seconds, max_frames=args.max_frames)
    if config:
        if "game_id" in config:
            output["game_id"] = config["game_id"]
        if "video_duration_seconds" in config and abs(
                output["video_duration_seconds"] - float(config["video_duration_seconds"])) > 3:
            raise ValueError("CPU analysis video is different from ROI config")
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"CPU frames={output['analysis']['frames_processed']}, "
          f"candidates={len(output['candidates'])}, "
          f"seconds={output['analysis']['elapsed_seconds']}, "
          f"partial={output['analysis']['incomplete_scan']}")


if __name__ == "__main__":
    main()
