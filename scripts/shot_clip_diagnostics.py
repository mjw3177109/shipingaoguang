#!/usr/bin/env python3
"""Inspect real basketball clips on CPU, preserving failures and zero-candidate results.

Not a shot classifier. A free-throw clip is a weak clip-level label from the
Wikimedia source description; actual release frame/time is NOT automatically known.
"""
import argparse
import json
import math
from pathlib import Path

from highlights.vision.cpu import scan_cpu_video, _colored_ball_candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--description-label", choices=("free_throw", "passing"), required=True)
    parser.add_argument("--samples-per-second", type=float, default=4)
    args = parser.parse_args()
    target = Path(args.out)
    target.mkdir(parents=True, exist_ok=True)
    output = scan_cpu_video(args.video, sample_seconds=1 / args.samples_per_second)
    (target / "cpu_proposals.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    # Use a simple contact sheet for manual inspection of footage/frame-level claims.
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise ValueError("Cannot open footage for contact sheet")
    dur = output["video_duration_seconds"]
    n = max(4, min(12, int(math.ceil(dur * 2))))
    imgs = []
    annotated = []
    observations_per_frame = []
    try:
        for i in range(n):
            t = min(dur - .05, (i+.5) * dur/n)
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000*t)
            ok, frame = cap.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            factor = 320 / max(width, 1)
            resized = cv2.resize(frame, (320, max(60, round(height*factor))))
            resized = cv2.copyMakeBorder(resized, 0, 28, 0, 0,
                                         cv2.BORDER_CONSTANT, value=(20,20,20))
            cv2.putText(resized, f"{t:.2f}s", (8,18),
                        cv2.FONT_HERSHEY_SIMPLEX, .52, (255,255,255), 1)
            imgs.append(resized)
            marked = resized.copy()
            detections = _colored_ball_candidates(frame, cv2, np)
            observations_per_frame.append({
                "time_seconds": round(t, 3),
                "orange_round_objects": len(detections),
                "normalized_centers": [[round(x,3), round(y,3), round(q,3)]
                                       for x,y,q in detections],
            })
            for x,y,q in detections:
                cx = int(x*marked.shape[1])
                cy = int(y*(marked.shape[0]-28))
                cv2.circle(marked, (cx,cy), 7, (0,255,0), 2)
                cv2.putText(marked, f"{q:.2f}", (cx+6,cy-6),
                            cv2.FONT_HERSHEY_SIMPLEX, .32, (0,255,0), 1)
            annotated.append(marked)
    finally:
        cap.release()
    if imgs:
        max_height = max(img.shape[0] for img in imgs)
        padded = [cv2.copyMakeBorder(img, 0, max_height-img.shape[0], 0, 0,
                                    cv2.BORDER_CONSTANT, value=(20,20,20)) for img in imgs]
        rows = []
        for i in range(0, len(padded), 3):
            group = padded[i:i+3]
            while len(group) < 3:
                group.append(np.zeros_like(padded[0]))
            rows.append(np.concatenate(group, axis=1))
        sheet = np.concatenate(rows, axis=0)
        if not cv2.imwrite(str(target / "contact_sheet.jpg"), sheet):
            raise ValueError("Could not write contact sheet")
        marked_rows = []
        for i in range(0,len(annotated),3):
            group = annotated[i:i+3]
            group = [cv2.copyMakeBorder(img,0,max_height-img.shape[0],0,0,
                                       cv2.BORDER_CONSTANT,value=(20,20,20))
                     for img in group]
            while len(group)<3:
                group.append(np.zeros_like(padded[0]))
            marked_rows.append(np.concatenate(group,axis=1))
        if not cv2.imwrite(str(target/"annotated_detections.jpg"),
                          np.concatenate(marked_rows,axis=0)):
            raise ValueError("Could not write detected objects visualization")

    (target / "frame_observations.json").write_text(json.dumps(
        observations_per_frame, ensure_ascii=False, indent=2))
    result = {
        "source_url": args.source_url,
        "license": args.license,
        "source_description_label": args.description_label,
        "source_clip_duration_seconds": dur,
        "cpu_activity_proposals": len(output["candidates"]),
        "candidate_intervals": [
            {"start_seconds": x["start_seconds"], "end_seconds": x["end_seconds"]}
            for x in output["candidates"]],
        "frame_samples_inspectable": len(imgs),
        "has_candidate": bool(output["candidates"]),
        "shot_detection_accuracy": None,
        "important": "Ball activity proposals are not shot attempts; free_throw label was read from source description, not manually localized frame-level ground truth.",
    }
    (target / "diagnostics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
