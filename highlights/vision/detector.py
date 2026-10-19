"""torchvision COCO ball detector, optional dependencies imported only by CLI.

This is NOT a basketball shot classification model. For research GPU notebooks.
Review training checkpoints' usage conditions before production deployment.
"""
import math
import time

from .events import BallObservation, propose_shots, validate_hoops


def scan_video(video_path, hoops, *, interval=1.5, max_frames=1800,
               threshold=.35, device="auto", progress=200):
    # Main Django image is deliberately CPU-only and does not include torch.
    import cv2
    import torch
    from torchvision.models.detection import (
        fasterrcnn_resnet50_fpn, FasterRCNN_ResNet50_FPN_Weights,
    )

    hoops = validate_hoops(hoops)
    if not (.4 <= interval <= 10 and 1 <= max_frames <= 10000
            and .05 <= threshold <= .99):
        raise ValueError("采样间隔、帧数或阈值超出允许范围。")
    if device not in ("auto", "cuda", "cpu"):
        raise ValueError("device 必须是 auto、cuda 或 cpu。")
    use_cuda = torch.cuda.is_available() if device == "auto" else device == "cuda"
    if use_cuda and not torch.cuda.is_available():
        raise RuntimeError("指定了 CUDA，但运行环境没有可用的 GPU。")
    device_name = "cuda" if use_cuda else "cpu"

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError("无法打开视频，请检查输入路径。")
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not (math.isfinite(fps) and math.isfinite(count) and fps > 0 and count > 0):
            raise ValueError("视频元数据无法提供有效的 FPS/时长。")
        duration = count / fps
        total_samples = math.ceil(duration / interval)
        scheduled = min(total_samples, max_frames)
        truncated = total_samples > max_frames
        started = time.monotonic()
        weights = FasterRCNN_ResNet50_FPN_Weights.DEFAULT
        sports_ball_id = weights.meta["categories"].index("sports ball")
        model = fasterrcnn_resnet50_fpn(weights=weights).to(device_name).eval()
        preprocess = weights.transforms()
        observations = []
        processed = 0
        for index in range(scheduled):
            t = index * interval
            capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            # Keep aspect ratio; faster input while preserving normalized points.
            if max(width, height) > 960:
                factor = 960 / max(width, height)
                frame = cv2.resize(frame, (round(width * factor), round(height * factor)))
                height, width = frame.shape[:2]
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            tensor = torch.from_numpy(frame.copy()).permute(2, 0, 1).contiguous()
            with torch.inference_mode():
                predicted = model([preprocess(tensor).to(device_name)])[0]
            processed += 1
            for box, cls, score in zip(predicted["boxes"], predicted["labels"], predicted["scores"]):
                if int(cls.item()) != sports_ball_id or float(score.item()) < threshold:
                    continue
                x1, y1, x2, y2 = box.tolist()
                observations.append(BallObservation(
                    time=t, x=min(1, max(0, (x1+x2)/(2*width))),
                    y=min(1, max(0, (y1+y2)/(2*height))),
                    confidence=float(score.item()),
                ))
            if progress and (index + 1) % progress == 0:
                print(f"scanned {index+1}/{scheduled} frames; observations={len(observations)}", flush=True)
        return {
            "schema_version": 2,
            "video_duration_seconds": round(duration, 3),
            "analysis": {
                "kind": "ball_near_hoop_candidate",
                "model": "torchvision_fasterrcnn_resnet50_fpn_coco",
                "device": device_name,
                "sample_interval_seconds": interval,
                "frames_scheduled": scheduled,
                "frames_processed": processed,
                "observations": len(observations),
                "incomplete_scan": truncated,
                "analyzed_until_seconds": round(min(duration, scheduled * interval), 2),
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "warning": "候选不是投篮命中识别结果；漏检和误报需要人工审核。",
            },
            "hoops": hoops,
            "candidates": propose_shots(observations, hoops, duration),
        }
    finally:
        capture.release()
