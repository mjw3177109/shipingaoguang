"""Event-level offline evaluation of UNREVIEWED proposals against independent human labels.

Only evaluate fully annotated time spans. This is not a scored-basket classifier.
Pure stdlib: runs on CPU GitHub Actions as well as Kaggle.
"""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def _num(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _duration(payload):
    return _num(payload.get("video_duration_seconds"), "video_duration_seconds")


def validate_truth(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Ground truth must use schema_version=1")
    duration = _duration(data)
    if duration <= 0 or duration > 6 * 3600:
        raise ValueError("Invalid video_duration_seconds")
    if data.get("complete_within_scope") is not True:
        raise ValueError("Complete annotation confirmation is required. Label ALL attempts in the evaluation scope.")
    events = data.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be an array (empty is allowed for fully labeled no-shot footage)")
    times = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "shot_attempt":
            raise ValueError("Only shot_attempt event annotations are supported")
        t = _num(event.get("time_seconds"), "event.time_seconds")
        if t < 0 or t > duration:
            raise ValueError("Ground-truth event outside video duration")
        times.append(t)
    return duration, sorted(times)


def validate_proposals(data):
    if not isinstance(data, dict) or data.get("schema_version") not in (1, 2):
        raise ValueError("Unsupported proposals JSON schema")
    duration = _duration(data)
    if not 0 < duration <= 6 * 3600:
        raise ValueError("Invalid proposal duration")
    items = data.get("candidates")
    if not isinstance(items, list):
        raise ValueError("candidates must be an array")
    clips = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("candidate must be an object")
        start = _num(item.get("start_seconds"), "candidate.start_seconds")
        end = _num(item.get("end_seconds"), "candidate.end_seconds")
        if not (0 <= start < end <= duration + .05):
            raise ValueError("Invalid candidate time span")
        clips.append((start, min(end, duration)))
    analysis = data.get("analysis", {})
    if not isinstance(analysis, dict):
        raise ValueError("analysis must be an object")
    partial = analysis.get("incomplete_scan", False)
    if not isinstance(partial, bool):
        raise ValueError("incomplete_scan must be boolean")
    scanned_until = _num(analysis.get("analyzed_until_seconds", duration), "analyzed_until_seconds")
    if not (0 < scanned_until <= duration + .05):
        raise ValueError("Invalid analyzed_until_seconds")
    if partial and scanned_until >= duration - .05:
        # Still incomplete if metadata says so; do not accidentally promote to full-video metrics.
        pass
    return duration, clips, min(duration, scanned_until), partial, analysis


def _maximum_matching(event_times, clips, tolerance=0):
    """1:1 bipartite matching maximizes TP count rather than relying on greedy order."""
    possible = []
    for t in event_times:
        options = [(idx, abs((start + end) / 2 - t))
                   for idx, (start, end) in enumerate(clips)
                   if start - tolerance <= t <= end + tolerance]
        options.sort(key=lambda pair: pair[1])
        possible.append([idx for idx, _ in options])
    owner = {}

    def assign(gt, used):
        for candidate in possible[gt]:
            if candidate in used:
                continue
            used.add(candidate)
            if candidate not in owner or assign(owner[candidate], used):
                owner[candidate] = gt
                return True
        return False

    for gt in range(len(event_times)):
        assign(gt, set())
    return len(owner), set(owner), set(owner.values())


def evaluate(proposals, ground_truth, *, tolerance=0, gpu_hourly_usd=None):
    gt_duration, event_times = validate_truth(ground_truth)
    duration, candidates, analyzed_until, is_incomplete, analysis = validate_proposals(proposals)
    if abs(gt_duration - duration) > 3:
        raise ValueError("Video duration mismatch, do not evaluate different recordings")
    if ("game_id" in proposals and "game_id" in ground_truth
            and str(proposals["game_id"]) != str(ground_truth["game_id"])):
        raise ValueError("game_id mismatch")
    tolerance = _num(tolerance, "tolerance")
    if not 0 <= tolerance <= 3:
        raise ValueError("Allowed tolerance is 0..3 seconds")
    # No cheating: if only a prefix was analyzed, do not count unseen events as model misses.
    scope = analyzed_until if is_incomplete else min(duration, gt_duration)
    gt_scoped = [t for t in event_times if t <= scope]
    candidates_scoped = [(s, e) for s, e in candidates if s < scope]
    candidates_scoped = [(s, min(e, scope)) for s, e in candidates_scoped]
    matched, matching_candidates, matching_events = _maximum_matching(
        gt_scoped, candidates_scoped, tolerance)
    missed = len(gt_scoped) - matched
    false_positive = len(candidates_scoped) - matched
    precision = matched / len(candidates_scoped) if candidates_scoped else (
        1.0 if not gt_scoped else 0.0)
    recall = matched / len(gt_scoped) if gt_scoped else None
    f1 = (2 * precision * recall / (precision + recall)
          if recall is not None and precision + recall > 0 else None)
    event_coverage = sum(any(s - tolerance <= t <= e + tolerance
                             for s, e in candidates_scoped) for t in gt_scoped)
    # Both miss count and event_coverage are exposed: if multiple shots in one long clip,
    # one-to-one matching penalizes under-segmentation (intentional).
    elapsed = analysis.get("elapsed_seconds")
    elapsed = _num(elapsed, "elapsed_seconds") if elapsed is not None else None
    if elapsed is not None and elapsed < 0:
        raise ValueError("elapsed_seconds cannot be negative")
    if gpu_hourly_usd is not None:
        gpu_hourly_usd = _num(gpu_hourly_usd, "gpu_hourly_usd")
        if gpu_hourly_usd < 0:
            raise ValueError("GPU price cannot be negative")
    process_cost = (round(elapsed / 3600 * gpu_hourly_usd, 4)
                    if elapsed is not None and gpu_hourly_usd is not None else None)
    false_positive_per_10min = round(false_positive / (scope / 600), 3) if scope > 0 else None
    metrics = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"seconds": round(scope, 3), "full_video": not is_incomplete,
                  "complete_ground_truth_confirmed": True,
                  "excluded_unscanned_events": len(event_times) - len(gt_scoped)},
        "performance": {
            "ground_truth_shots": len(gt_scoped),
            "candidate_clips": len(candidates_scoped),
            "true_positive_matches": matched,
            "missed_shots": missed,
            "false_positive_clips": false_positive,
            "precision": round(precision, 4),
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "raw_event_coverage": round(event_coverage / len(gt_scoped), 4) if gt_scoped else None,
            "false_positives_per_10min": false_positive_per_10min,
            "unmatched_truth_timestamps": [
                round(t, 3) for index, t in enumerate(gt_scoped)
                if index not in matching_events],
            "unmatched_candidate_intervals": [
                {"start_seconds": round(s, 3), "end_seconds": round(e, 3)}
                for index, (s, e) in enumerate(candidates_scoped)
                if index not in matching_candidates],
        },
        "compute": {
            "elapsed_seconds": elapsed,
            "video_minutes_per_wall_minute": round(scope / elapsed, 3)
            if elapsed and elapsed > 0 else None,
            "gpu_hourly_usd": gpu_hourly_usd,
            "estimated_gpu_usd": process_cost,
            "gpu_price_excludes_storage_and_traffic": True,
        },
        "notes": [
            "One-to-one matching: multiple annotated shots in one long clip are not all counted as matches.",
            "Precision/recall measure candidate retrieval against human shot_attempt labels, NOT made baskets.",
            "Partially scanned videos are evaluated only over the analyzed prefix.",
            "No benchmark is trustworthy without complete independent labels and representative footage.",
        ],
    }
    return metrics


def format_report(report):
    p, s, c = report["performance"], report["scope"], report["compute"]
    def fmt(v):
        return "N/A" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)
    return "\n".join([
        "# BasketCut V0.2 检索评估报告",
        "",
        f"- 评估范围：{fmt(s['seconds'])} 秒；全片覆盖：{'是' if s['full_video'] else '否（仅评估已分析前缀）'}",
        f"- 人工标注投篮尝试：{p['ground_truth_shots']} 次",
        f"- 算法候选：{p['candidate_clips']} 段",
        f"- 一对一匹配：{p['true_positive_matches']} 次",
        f"- 漏检：{p['missed_shots']} 次；误报候选：{p['false_positive_clips']} 段",
        f"- Precision：{fmt(p['precision'])}；Recall：{fmt(p['recall'])}；F1：{fmt(p['f1'])}",
        f"- 每10分钟误报：{fmt(p['false_positives_per_10min'])}",
        f"- 实际分析耗时：{fmt(c['elapsed_seconds'])} 秒；估算 GPU 费用：{fmt(c['estimated_gpu_usd'])} USD",
        "",
        "## 必须说明",
        "此报告仅评估投篮尝试候选检索，不能当作自动进球判定准确率。",
        "如果视频只分析一部分，结果不代表整场比赛。",
        "请用与调参数据不同的比赛做最终盲测，避免测试集过拟合。",
        "",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate candidate shots vs exhaustive human labels")
    parser.add_argument("--proposals", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--out", required=True, help="Directory for evaluation.json and report.md")
    parser.add_argument("--tolerance", type=float, default=0.0)
    parser.add_argument("--gpu-hourly-usd", type=float, default=None)
    args = parser.parse_args(argv)
    proposals = json.loads(Path(args.proposals).read_text(encoding="utf-8"))
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    report = evaluate(proposals, truth, tolerance=args.tolerance,
                      gpu_hourly_usd=args.gpu_hourly_usd)
    directory = Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "report.md").write_text(format_report(report), encoding="utf-8")
    print(format_report(report))


if __name__ == "__main__":
    main()
