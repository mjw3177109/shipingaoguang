"""Deterministic game-level TRAIN / VAL / sealed TEST split.

No image/frame-level shuffle is permitted. Test split is never read by
train_specialist.py, and cannot be used to choose hyperparameters.
"""
import hashlib
import math


def three_way_games(game_ids, val_ratio=.20, test_ratio=.20):
    ids = list(game_ids)
    if len(ids) < 6 or len(set(ids)) != len(ids):
        raise ValueError("V0.5 independent test requires at least six distinct games")
    if not (0.1 <= val_ratio <= .3 and 0.1 <= test_ratio <= .3
            and val_ratio + test_ratio <= .6):
        raise ValueError("val/test ratios must be 0.1..0.3 with total <= 0.6")
    ranked = sorted(ids, key=lambda g: (
        hashlib.sha256(f"basketcut-split-v05:{g}".encode()).hexdigest(), str(g)))
    n_test = max(2, math.floor(len(ids) * test_ratio))
    n_val = max(2, math.floor(len(ids) * val_ratio))
    if len(ids) - n_test - n_val < 2:
        raise ValueError("Need at least two training games after independent splits")
    result = {}
    for i, game_id in enumerate(ranked):
        result[game_id] = ("test" if i < n_test else
                           "val" if i < n_test + n_val else "train")
    return result


def audit_three_way(games, *, min_games=8, min_positive=120,
                    min_negative=40, min_test_positive=30,
                    min_test_negative=20):
    rows = list(games)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate game id")
    if any(r["split"] not in ("train", "val", "test") or
           not isinstance(r["positive"], int) or
           not isinstance(r["negative"], int) or
           r["positive"] < 0 or r["negative"] < 0 for r in rows):
        raise ValueError("Invalid game audit counts")
    group = {k: [r for r in rows if r["split"] == k]
             for k in ("train", "val", "test")}
    npos = sum(r["positive"] for r in rows)
    nneg = sum(r["negative"] for r in rows)
    tp = sum(r["positive"] for r in group["test"])
    tn = sum(r["negative"] for r in group["test"])
    checks = {
        "enough_games": len(rows) >= min_games,
        "train_games_at_least_two": len(group["train"]) >= 2,
        "val_games_at_least_two": len(group["val"]) >= 2,
        "test_games_at_least_two": len(group["test"]) >= 2,
        "enough_positive_frames": npos >= min_positive,
        "enough_negative_frames": nneg >= min_negative,
        "test_has_enough_positive": tp >= min_test_positive,
        "test_has_enough_negative": tn >= min_test_negative,
    }
    for name in ("train", "val", "test"):
        checks[f"{name}_has_both_classes"] = (
            any(r["positive"] > 0 for r in group[name]) and
            any(r["negative"] > 0 for r in group[name]))
    return {
        "schema_version": 2, "split": "game_disjoint_train_val_sealed_test",
        "games": len(rows), "positive": npos, "explicit_negative": nneg,
        "by_split": {
            name: {"games": len(items),
                   "positive": sum(r["positive"] for r in items),
                   "negative": sum(r["negative"] for r in items)}
            for name, items in group.items()},
        "checks": checks,
        "data_coverage_ready": all(checks.values()),
        "warning": "Data readiness is NOT object detection accuracy or model release authorization.",
    }
