"""Optional truly pretrained COCO sports-ball detector on CPU (no GPU required).

Research only: an object detector is NOT an event/action classifier. A lack of
detected balls is possible with small/far basketballs. The primary Django web
container deliberately does not install torch; run this offline and import
the proposal JSON for human review.
"""
import argparse
import json
import math
import os
import time
from pathlib import Path

from .cpu import _activity_candidates


def scan_pretrained_cpu(video_path, *, interval=.8, max_frames=24,
                        min_confidence=.16, architecture="mobile320", tiles=1):
    import cv2
    import torch
    from torchvision.models.detection import (
        fasterrcnn_mobilenet_v3_large_320_fpn,
        FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
        fasterrcnn_resnet50_fpn,
        FasterRCNN_ResNet50_FPN_Weights,
    )
    from .events import BallObservation

    if not (.35 <= interval <= 5 and 1 <= max_frames <= 1800 and
            .05 <= min_confidence <= .95):
        raise ValueError("Bad inference parameters")
    if tiles not in (1, 4):
        raise ValueError("tiles must be 1 or 4")
    if architecture not in ("mobile320", "resnet50"):
        raise ValueError("architecture must be mobile320 or resnet50")
    torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
    start_wall = time.monotonic()
    if architecture == "mobile320":
        weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
        model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights)
    else:
        weights = FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        model = fasterrcnn_resnet50_fpn(weights=weights)
    model.eval().to("cpu")
    preprocess = weights.transforms()
    ball_class_id = weights.meta["categories"].index("sports ball")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not open video")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or not math.isfinite(count) or fps <= 0 or count < 1:
            raise ValueError("Invalid source video FPS")
        duration = count / fps
        count_scheduled = min(math.ceil(duration / interval), max_frames)
        observations = []
        decoded = 0
        last_second = 0
        for i in range(count_scheduled):
            second = i * interval
            cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            valid, frame = cap.read()
            if not valid:
                break
            decoded += 1
            last_second = second
            height, width = frame.shape[:2]
            if max(width, height) > 1280:
                scale = 1280 / max(width, height)
                frame = cv2.resize(frame, (max(1,round(width*scale)),
                                           max(1,round(height*scale))))
                height, width = frame.shape[:2]
            crops = [(0,0,width,height)]
            if tiles == 4:
                # Overlapping quadrants reduce the severe scale loss of distant balls.
                # Deduplicate same-frame centers after inverse coordinate mapping.
                crops = []
                xsize = max(1, round(width * .6))
                ysize = max(1, round(height * .6))
                for ox in (0, max(0,width-xsize)):
                    for oy in (0, max(0,height-ysize)):
                        crops.append((ox,oy,min(width,ox+xsize),min(height,oy+ysize)))
            candidates_for_frame = []
            for ox,oy,rx,ry in crops:
                crop = frame[oy:ry,ox:rx]
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                tensor = torch.from_numpy(rgb.copy()).permute(2,0,1).contiguous()
                with torch.inference_mode():
                    prediction = model([preprocess(tensor)])[0]
                for box, label, conf in zip(
                        prediction["boxes"], prediction["labels"], prediction["scores"]):
                    if int(label.item()) != ball_class_id or conf.item() < min_confidence:
                        continue
                    x1,y1,x2,y2 = box.tolist()
                    cx = (ox+(x1+x2)/2)/width
                    cy = (oy+(y1+y2)/2)/height
                    if not (0 <= cx <= 1 and 0 <= cy <= 1):
                        continue
                    candidates_for_frame.append((cx,cy,float(conf.item())))
            candidates_for_frame.sort(key=lambda c:-c[2])
            picked = []
            for x,y,q in candidates_for_frame:
                if all(math.hypot(x-px,y-py) > .025 for px,py,_ in picked):
                    picked.append((x,y,q))
                    observations.append(BallObservation(second,x,y,q))
        if decoded == 0:
            raise ValueError("No decoded video frames")
        scoped = min(duration, last_second+interval)
        incomplete = scoped < duration - .05
        proposals = _activity_candidates(observations, duration)
        for clip in proposals:
            clip["source"] = "model_proposal"
            clip["note"] = "CPU预训练模型检出运动球候选，非投篮或命中结论；必须人工核对"
        return {
            "schema_version": 2,
            "video_duration_seconds": round(duration, 3),
            "analysis": {
                "kind": "pretrained_coco_sports_ball_cpu",
                "model": f"torchvision_fasterrcnn_{architecture}",
                "tiles_per_frame": tiles,
                "device": "cpu",
                "sample_interval_seconds": interval,
                "frames_processed": decoded,
                "ball_detections": len(observations),
                "analyzed_until_seconds": round(scoped, 3),
                "incomplete_scan": incomplete,
                "elapsed_seconds": round(time.monotonic()-start_wall, 2),
                "warning": "COCO sports ball detection is not shot detection. Confirm every clip.",
            },
            "observations": [
                {"time":round(o.time,3),"x":round(o.x,5),"y":round(o.y,5),
                 "confidence":round(o.confidence,4)} for o in observations[:500]],
            "candidates": proposals,
        }
    finally:
        cap.release()


def main(argv=None):
    p = argparse.ArgumentParser(description="Pretrained COCO sports-ball on free CPU")
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--interval", type=float, default=.8)
    p.add_argument("--max-frames", type=int, default=24)
    p.add_argument("--threshold", type=float, default=.16)
    p.add_argument("--arch", choices=("mobile320","resnet50"), default="mobile320")
    p.add_argument("--tiles", choices=(1,4), type=int, default=1)
    args = p.parse_args(argv)
    if args.config and not config.get("hoops"):
        raise ValueError("--config must contain at least one hoop region")
    result = scan_pretrained_cpu(args.video, interval=args.interval,
               max_frames=args.max_frames, min_confidence=args.threshold,
               architecture=args.arch, tiles=args.tiles)
    dst = Path(args.out)
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(result["analysis"], indent=2,ensure_ascii=False))
    print(f"CPU-trained model proposed {len(result['candidates'])} review-only clips")


if __name__ == "__main__":
    main()
