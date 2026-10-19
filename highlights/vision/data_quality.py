"""Small-ball dataset quality assessment, strictly separate from model accuracy.

Passing this checklist does NOT prove a model is good. It only means the
annotation dataset clears basic coverage and game-disjoint validation gates.
"""


def audit_games(games, *, min_games=4, min_positive=120, min_negative=40):
    """games: iterable of {id, split, positive, negative, ...} dictionaries."""
    games = list(games)
    train = [g for g in games if g["split"] == "train"]
    val = [g for g in games if g["split"] == "val"]
    if len({g["id"] for g in games}) != len(games):
        raise ValueError("Game IDs must be unique for quality audit")
    if any(g["split"] not in ("train","val") or g["positive"] < 0
           or g["negative"] < 0 for g in games):
        raise ValueError("Bad split / negative label totals")
    positive = sum(g["positive"] for g in games)
    negative = sum(g["negative"] for g in games)
    conditions = {
        "at_least_four_games": len(games) >= min_games,
        "two_train_games": len(train) >= 2,
        "two_validation_games": len(val) >= 2,
        "enough_positive_frames": positive >= min_positive,
        "enough_explicit_negative_frames": negative >= min_negative,
        "train_has_both_classes": bool(train) and sum(g["positive"] for g in train) > 0 and sum(g["negative"] for g in train) > 0,
        "validation_has_both_classes": bool(val) and sum(g["positive"] for g in val) > 0 and sum(g["negative"] for g in val) > 0,
    }
    return {
        "schema_version":1,
        "games":len(games),
        "positive_frames":positive,
        "explicit_negative_frames":negative,
        "negative_fraction":round(negative/(positive+negative),4) if positive+negative else None,
        "train_games":len(train),"validation_games":len(val),
        "checks":conditions,
        "basic_data_coverage_ready":all(conditions.values()),
        "disclaimer":"Dataset coverage is NOT detection accuracy, production approval, or consent review.",
    }
