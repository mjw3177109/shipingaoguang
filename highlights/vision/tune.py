"""CPU-only parameter sweep using cached GPU ball observations.

Parameter optimization on labeled videos is development-set fitting, NOT independent
accuracy evidence. Validate the selected settings on different held-out games.
"""
import argparse
import json
import math
from pathlib import Path

from .events import BallObservation, propose_shots, validate_hoops
from .evaluate import evaluate


def _numbers(raw, cast, *, allow_zero=False):
    values = [cast(x.strip()) for x in raw.split(",") if x.strip()]
    if not values or any(not math.isfinite(v) or v < (0 if allow_zero else 0.001) for v in values):
        raise ValueError("Invalid sweep values")
    return sorted(set(values))


def search(raw, truth, *, min_hits=(1, 2, 3), confidence=(.2, .35, .5)):
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("observations.json must use schema_version=1")
    hoops = validate_hoops(raw.get("hoops"))
    duration = float(raw.get("video_duration_seconds"))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid video duration")
    rows = raw.get("observations")
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise ValueError("Invalid or overly large observations list")
    points = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Invalid observation")
        point = BallObservation(float(item["time"]), float(item["x"]),
                                float(item["y"]), float(item["confidence"]))
        if not all(math.isfinite(v) for v in (point.time, point.x, point.y, point.confidence)):
            raise ValueError("Non-finite observation")
        points.append(point)
    if not isinstance(raw.get("analysis"), dict):
        raise ValueError("Missing analysis metadata")
    if len(min_hits) * len(confidence) > 100:
        raise ValueError("Too many parameter combinations")
    if any(not 1 <= hits <= 10 for hits in min_hits):
        raise ValueError("min_hits must be 1..10")
    if any(not 0 <= score <= 1 for score in confidence):
        raise ValueError("confidence must be 0..1")
    results = []
    for hits in min_hits:
        for threshold in confidence:
            proposals = {
                "schema_version": 2, "game_id": raw.get("game_id"),
                "video_duration_seconds": duration,
                "analysis": dict(raw["analysis"], replay_min_hits=hits, replay_min_confidence=threshold),
                "candidates": propose_shots(points, hoops, duration,
                                            min_hits=hits, min_confidence=threshold),
            }
            report = evaluate(proposals, truth)
            metric = report["performance"]
            results.append({
                "min_hits": hits, "min_confidence": threshold,
                "candidate_count": metric["candidate_clips"],
                "precision": metric["precision"], "recall": metric["recall"],
                "f1": metric["f1"],
                "false_positives_per_10min": metric["false_positives_per_10min"],
                "report": report,
                "proposals": proposals,
            })
    # F1 encourages balance; ties reward recall, then fewer false positives.
    results.sort(key=lambda r: (
        r["f1"] if r["f1"] is not None else -1,
        r["recall"] if r["recall"] is not None else -1,
        -r["false_positives_per_10min"],
        -r["candidate_count"],
    ), reverse=True)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="CPU-only sweep on labeled DEVELOPMENT videos")
    parser.add_argument("--observations", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-hits", default="1,2,3")
    parser.add_argument("--confidence", default="0.2,0.35,0.5")
    args = parser.parse_args(argv)
    raw = json.loads(Path(args.observations).read_text(encoding="utf-8"))
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    rows = search(raw, truth,
                  min_hits=_numbers(args.min_hits, int),
                  confidence=_numbers(args.confidence, float, allow_zero=True))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "warning": "DEV-set results only; MUST rerun fixed best parameters on held-out games.",
        "best": {key: rows[0][key] for key in ("min_hits", "min_confidence",
                  "candidate_count", "precision", "recall", "f1", "false_positives_per_10min")},
        "grid": [{key: row[key] for key in ("min_hits", "min_confidence",
                  "candidate_count", "precision", "recall", "f1", "false_positives_per_10min")}
                 for row in rows],
    }
    (out / "tuning.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "best_proposals.json").write_text(json.dumps(rows[0]["proposals"], indent=2, ensure_ascii=False),
                                            encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("Note: the best run is selected on DEVELOPMENT footage; its metrics are optimistically biased.")


if __name__ == "__main__":
    main()
