"""Bounded, no-download CPU *annotation suggestions* for basketball video.

These suggestions are NEVER labels. Humans must determine basketball present or
absent and draw boxes. Uses visual diversity / orange-like ambiguity / motion.
Does not attempt scoring, identity or human event classification.
"""
import argparse
import json
import math
import time
from pathlib import Path

REASONS = ("orange_ambiguous", "motion", "coverage")


def choose_suggestions(entries, duration, limit=24):
    """Deterministic quota selection with temporal de-duplication."""
    if not 1 <= limit <= 100 or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid sampler limit / duration")
    ordered = {reason: [] for reason in REASONS}
    for item in entries:
        t = float(item["time_seconds"])
        score = float(item["score"])
        reason = item["reason"]
        if reason not in REASONS or not all(math.isfinite(v) for v in (t, score)):
            raise ValueError("Invalid CPU frame observation")
        if 0 <= t < duration:
            ordered[reason].append({"time_seconds": round(t, 2),
                                    "score": round(max(0, min(1, score)), 4),
                                    "reason": reason})
    # Explicit coverage quota ensures model failures and blank scenes get reviewed.
    quotas = {"orange_ambiguous": math.ceil(limit * .5),
              "motion": math.ceil(limit * .3),
              "coverage": limit}
    for reason in REASONS:
        ordered[reason].sort(key=lambda x:(-x["score"],x["time_seconds"]))
    selected=[]
    gap = max(.35, min(3., duration / (limit * 2)))
    def append(item):
        if all(abs(item["time_seconds"] - prior["time_seconds"]) >= gap
               for prior in selected):
            selected.append(item)
            return True
        return False
    for reason in REASONS:
        count=0
        for item in ordered[reason]:
            if len(selected)>=limit or count>=quotas[reason]:
                break
            if append(item):
                count+=1
    # If one category lacks samples, use other categories without inventing labels.
    candidates=sorted((x for group in ordered.values() for x in group),
                      key=lambda x:(-x["score"],x["time_seconds"]))
    for item in candidates:
        if len(selected)>=limit:
            break
        append(item)
    return sorted(selected,key=lambda x:x["time_seconds"])


def scan_annotation_frames(video_path, *, limit=24, max_samples=96):
    import cv2
    import numpy as np
    from .cpu import _colored_ball_candidates
    if not (1 <= limit <= 100 and 8 <= max_samples <= 180):
        raise ValueError("Invalid bounded CPU sampler parameters")
    cap=cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Cannot open video for CPU annotation sampling")
    started=time.monotonic()
    try:
        fps=float(cap.get(cv2.CAP_PROP_FPS))
        frames=float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not math.isfinite(fps) or not math.isfinite(frames) or fps<=0 or frames<=0:
            raise ValueError("Invalid media FPS or frame count")
        duration=frames/fps
        if not 1 <= duration <= 21600:
            raise ValueError("Video must be 1s..6h")
        num=min(max_samples,max(8,math.ceil(duration / 2)))
        observations=[]
        previous=None
        decoded=0
        for index in range(num):
            t=(index+.35)*duration/num
            if t>=duration:
                continue
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000*t)
            ok,frame=cap.read()
            if not ok:
                continue
            decoded+=1
            h,w=frame.shape[:2]
            gray=cv2.cvtColor(cv2.resize(frame,(160,90)),cv2.COLOR_BGR2GRAY)
            change=(float(np.mean(cv2.absdiff(previous,gray)))/255
                    if previous is not None else 0.0)
            previous=gray
            balls=_colored_ball_candidates(frame,cv2,np)
            if len(balls) >= 1:
                # Orange-shape presence is ambiguous: often jersey, wood or signs.
                reason="orange_ambiguous"
                score=min(1., .3 + .12*len(balls) + .25*change +
                          .3*max(q for _,_,q in balls))
            elif change > .055:
                reason="motion"
                score=min(1., .3+change*2)
            else:
                reason="coverage"
                score=.2 + min(.2,change)
            observations.append({"time_seconds":t,"reason":reason,"score":score})
        if not observations:
            raise ValueError("No decoded video frames")
        suggestions=choose_suggestions(observations,duration,limit)
        return {
            "schema_version":1, "video_duration_seconds":round(duration,3),
            "analysis":{"algorithm":"bounded_cpu_ambiguous_orange_motion_diversity",
                        "device":"cpu","decoded_frames":decoded,
                        "elapsed_seconds":round(time.monotonic()-started,3),
                        "warning":"Only suggested REVIEW TIMES; no frame is automatically labeled ball or no-ball."},
            "suggestions":suggestions,
        }
    finally:
        cap.release()


def main(argv=None):
    p=argparse.ArgumentParser(description="CPU candidate-frame sampling for human box annotation")
    p.add_argument("--video",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--limit",type=int,default=24)
    p.add_argument("--max-samples",type=int,default=96)
    args=p.parse_args(argv)
    data=scan_annotation_frames(args.video,limit=args.limit,max_samples=args.max_samples)
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Decoded {data['analysis']['decoded_frames']} CPU frames; "
          f"suggested {len(data['suggestions'])} frames requiring human annotation")


if __name__=="__main__":
    main()
