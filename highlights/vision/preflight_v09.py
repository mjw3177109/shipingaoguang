"""V0.9 fail-closed inspection of consented human basketball box datasets.

This is a DATA eligibility check, not model accuracy. Never inspect sealed test
for hyperparameter selection; this routine checks only integrity and counts.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from .split_v05 import audit_three_way


def _safe_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Bad dataset-relative path")
    target = (root / relative).resolve()
    if root not in target.parents or not target.is_file():
        raise ValueError("Dataset file missing or outside dataset root")
    return target


def inspect_dataset(dataset):
    root = Path(dataset).resolve()
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("samples"), list):
        raise ValueError("Not a BasketCut human-labeled dataset")
    games = manifest.get("games", [])
    samples = manifest["samples"]
    if not isinstance(games, list) or len(samples) > 50000:
        raise ValueError("Malformed or oversized manifest")
    reasons = []
    counts = Counter()
    checks = {}
    def check(name, condition, reason):
        checks[name] = bool(condition)
        if not condition:
            reasons.append(reason)

    check("one_basketball_class", manifest.get("schema_version") == 1
          and manifest.get("classes") == ["basketball"], "错误的数据集类别/版本")
    check("explicitly_not_synthetic", manifest.get("synthetic") is not True
          and "SYNTHETIC" not in str(manifest.get("WARNING", "")).upper(),
          "合成工程测试数据不能用于真实训练验收")
    check("three_game_disjoint_partitions",
          manifest.get("test_sealed") is True and
          manifest.get("method") == "three_way_sealed_test_v05",
          "需要按整场比赛划分的 train/val/test 封存测试集")
    check("secondary_review_export", manifest.get("label_review_required") is True,
          "需要使用 --three-way --verified-only 导出")
    game_lookup = {}
    for g in games:
        if not isinstance(g, dict) or type(g.get("id")) not in (int, str):
            raise ValueError("Malformed game data")
        key = str(g["id"])
        if key in game_lookup:
            raise ValueError("Duplicate game ID")
        game_lookup[key] = g
        if g.get("split") not in ("train", "val", "test"):
            raise ValueError("Invalid game partition")
    check("consented_games",
          bool(games) and all(isinstance(g.get("training_rights"), str)
              and len(g["training_rights"].strip()) >= 12 for g in games),
          "至少一场比赛未提供有效训练权利依据记录")
    observed = {key: {"positive": 0, "negative": 0} for key in game_lookup}
    image_hash_splits = {}
    image_paths = set()
    format_errors = []
    for index, row in enumerate(samples):
        if not isinstance(row, dict):
            format_errors.append(f"sample[{index}] 格式错误")
            continue
        split = row.get("split")
        game = game_lookup.get(str(row.get("game_id")))
        if game is None or split != game["split"]:
            format_errors.append(f"sample[{index}] 跨比赛分区错误")
            continue
        if type(row.get("ball_absent")) is not bool:
            format_errors.append(f"sample[{index}] 缺少明确有球/无球标记")
            continue
        if not (row.get("quality_verified") is True and
                row.get("quality_verified_by_id") is not None and
                isinstance(row.get("quality_verified_at"), str) and row["quality_verified_at"]):
            format_errors.append(f"sample[{index}] 未二次复核")
        absence = row["ball_absent"]
        observed[str(row["game_id"])]["negative" if absence else "positive"] += 1
        counts["negative" if absence else "positive"] += 1
        try:
            image = row["image"]
            label = row["label"]
            if image in image_paths:
                raise ValueError("Duplicate image path")
            image_paths.add(image)
            if not image.startswith(f"images/{split}/") or not label.startswith(f"labels/{split}/"):
                raise ValueError("Image/label split path mismatch")
            data = _safe_path(root, image).read_bytes()
            if len(data) < 4 or not data.startswith(b"\xff\xd8\xff") or b"\xff\xd9" not in data[-4:]:
                raise ValueError("Not a valid JPEG envelope")
            digest = hashlib.sha256(data).hexdigest()
            if digest != row.get("sha256_image"):
                raise ValueError("JPEG hash differs from manifest")
            image_hash_splits.setdefault(digest, set()).add(split)
            label_lines = _safe_path(root, label).read_text(encoding="utf-8").splitlines()
            if absence and label_lines:
                raise ValueError("Explicit negative frame contains a box")
            if not absence and not label_lines:
                raise ValueError("Positive frame missing box")
            if len(label_lines) > 10:
                raise ValueError("Too many boxes per frame")
            for line in label_lines:
                parts = line.split()
                if len(parts) != 5 or parts[0] != "0":
                    raise ValueError("Not a one-class YOLO bbox")
                cx, cy, w, h = [float(v) for v in parts[1:]]
                if not (all(math.isfinite(v) for v in (cx, cy, w, h))
                        and 0 < w <= 1 and 0 < h <= 1
                        and 0 <= cx - w / 2 < cx + w / 2 <= 1.000001
                        and 0 <= cy - h / 2 < cy + h / 2 <= 1.000001):
                    raise ValueError("Invalid / out-of-range bbox")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            format_errors.append(f"sample[{index}]: {exc}")
    check("human_box_integrity", not format_errors,
          "逐帧标签/图片/复核元数据未通过检验")
    check("no_cross_partition_identical_frames",
          not any(len(splits) > 1 for splits in image_hash_splits.values()),
          "训练/验证/测试出现 SHA256 完全相同的图片")
    counts_match = (len(game_lookup) > 0 and
                    all(observed[key]["positive"] == g.get("positive")
                        and observed[key]["negative"] == g.get("negative")
                        and observed[key]["positive"] + observed[key]["negative"]
                          == g.get("sample_count", observed[key]["positive"] + observed[key]["negative"])
                        for key, g in game_lookup.items()))
    check("declared_counts_match_frames", counts_match,
          "比赛 manifest 正/负样本统计与实际标签不一致")
    try:
        audit = audit_three_way(games)
        check("independent_split_basic_coverage", audit["data_coverage_ready"],
              "未达到独立测试集与正负样本最小覆盖量")
    except (ValueError, KeyError, TypeError) as exc:
        audit = {}
        check("independent_split_basic_coverage", False, f"分组检查失败：{exc}")
    check("at_least_8_distinct_games", len(games) >= 8, "建议至少8场独立比赛")
    check("at_least_120_real_positive", counts["positive"] >= 120, "人工篮球正例不足120帧")
    check("at_least_40_explicit_negative", counts["negative"] >= 40, "人工无球负例不足40帧")
    return {
        "schema_version": 1,
        "purpose": "basketcut_v09_preflight",
        "dataset_manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "games": len(games), "frames": len(samples),
        "positive_frames": counts["positive"], "explicit_negative_frames": counts["negative"],
        "checks": checks, "blockers": reasons,
        "format_error_examples": format_errors[:8],
        "allow_cpu_research_experiment": all(checks.values()),
        "production_release": False,
        "warning": "Training/metric gate only. Counts, verified clicks and SHA checks do NOT certify accurate basket detection, consent legality, or commercial readiness.",
    }
