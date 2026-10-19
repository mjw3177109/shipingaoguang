#!/usr/bin/env python3
"""Inspect real basketball clips on CPU, preserving failures and zero-candidate results.

Not a shot classifier. A free-throw clip is a weak clip-level label from the
Wikimedia source description; actual release frame/time is NOT automatically known.
"""
import argparse
import json
import math
from pathlib import Path

from highlights.vision.cpu import scan_cpu_video


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
