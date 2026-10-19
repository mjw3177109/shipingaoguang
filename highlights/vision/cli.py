"""python -m highlights.vision.cli --video game.mp4 --config hoop_config.json --out candidates.json"""
import argparse
import json
from pathlib import Path

from .detector import scan_video
from .events import validate_hoops


def main(argv=None):
    parser = argparse.ArgumentParser(description="BasketCut V0.2: GPU ball-near-hoop candidate detector")
    parser.add_argument("--video", required=True, help="authorized basketball footage")
    parser.add_argument("--config", required=True, help="hoop configuration downloaded from Django")
    parser.add_argument("--out", required=True, help="output JSON for manual review")
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--max-frames", type=int, default=1800)
    parser.add_argument("--threshold", type=float, default=.35)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    hoops = validate_hoops(config["hoops"])
    result = scan_video(args.video, hoops, interval=args.interval,
                        max_frames=args.max_frames, threshold=args.threshold, device=args.device)
    expected = config.get("video_duration_seconds")
    if expected is not None and abs(float(expected) - result["video_duration_seconds"]) > 3:
        raise ValueError("配置与视频时长不匹配；请确保使用同一场比赛录像。")
    if "game_id" in config:
        result["game_id"] = config["game_id"]
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(result['candidates'])} unreviewed candidates: {destination}")
    if result["analysis"]["incomplete_scan"]:
        print("WARNING: max-frames limit reached; only part of video was analyzed.")


if __name__ == "__main__":
    main()
