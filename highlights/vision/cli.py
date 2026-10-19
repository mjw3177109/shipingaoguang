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
    parser.add_argument("--threshold", type=float, default=.20)
    parser.add_argument("--model", choices=("mobile320", "resnet50"), default="mobile320")
    parser.add_argument("--observations-out", default=None,
                        help="Optional JSON with ball positions to tune without rerunning GPU")
    parser.add_argument("--min-hits", type=int, default=2)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    hoops = validate_hoops(config["hoops"])
    result = scan_video(args.video, hoops, interval=args.interval,
                        max_frames=args.max_frames, threshold=args.threshold,
                        device=args.device, model_name=args.model,
                        include_observations=bool(args.observations_out), min_hits=args.min_hits)
    expected = config.get("video_duration_seconds")
    if expected is not None and abs(float(expected) - result["video_duration_seconds"]) > 3:
        raise ValueError("配置与视频时长不匹配；请确保使用同一场比赛录像。")
    if "game_id" in config:
        result["game_id"] = config["game_id"]
    if args.observations_out:
        raw_data = {
            "schema_version": 1,
            "video_duration_seconds": result["video_duration_seconds"],
            "game_id": result.get("game_id"),
            "hoops": result["hoops"],
            "analysis": result["analysis"],
            "observations": result.pop("sampled_observations"),
        }
        out_obs = Path(args.observations_out)
        out_obs.parent.mkdir(parents=True, exist_ok=True)
        out_obs.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved {len(raw_data['observations'])} ball observations for tuning: {out_obs}")
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {len(result['candidates'])} unreviewed candidates: {destination}")
    if result["analysis"]["incomplete_scan"]:
        print("WARNING: max-frames limit reached; only part of video was analyzed.")


if __name__ == "__main__":
    main()
