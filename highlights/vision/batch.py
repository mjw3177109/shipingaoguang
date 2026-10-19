"""Batch evaluator for multiple independently annotated basketball games.

Example: python -m highlights.vision.batch --manifest benchmarks/manifest.json --out reports/
"""
import argparse
import json
from pathlib import Path

from .evaluate import evaluate


def batch_evaluate(entries, base_dir, gpu_hourly_usd=None):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("manifest.games must contain 1..100 entries")
    results = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid game entry")
        name = entry.get("id")
        if not isinstance(name, str) or not name.strip() or name in seen:
            raise ValueError("Each game requires a unique string id")
        seen.add(name)
        proposal_path = (base_dir / entry["proposals"]).resolve()
        truth_path = (base_dir / entry["truth"]).resolve()
        proposals = json.loads(proposal_path.read_text(encoding="utf-8"))
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        report = evaluate(proposals, truth, gpu_hourly_usd=gpu_hourly_usd)
        results.append({"id": name, "report": report})
    totals = {"games": len(results), "fully_scanned_games": 0,
              "scope_minutes": 0, "shots": 0, "candidates": 0,
              "matches": 0, "missed": 0, "false_positive": 0,
              "estimated_gpu_usd": 0}
    cost_known = gpu_hourly_usd is not None
    for result in results:
        r = result["report"]
        s = r["scope"]
        p = r["performance"]
        c = r["compute"]
        totals["fully_scanned_games"] += int(s["full_video"])
        totals["scope_minutes"] += s["seconds"] / 60
        totals["shots"] += p["ground_truth_shots"]
        totals["candidates"] += p["candidate_clips"]
        totals["matches"] += p["true_positive_matches"]
        totals["missed"] += p["missed_shots"]
        totals["false_positive"] += p["false_positive_clips"]
        if c["estimated_gpu_usd"] is None:
            cost_known = False
        else:
            totals["estimated_gpu_usd"] += c["estimated_gpu_usd"]
    totals["scope_minutes"] = round(totals["scope_minutes"], 3)
    totals["precision_micro"] = round(totals["matches"] / totals["candidates"], 4) if totals["candidates"] else None
    totals["recall_micro"] = round(totals["matches"] / totals["shots"], 4) if totals["shots"] else None
    p, r = totals["precision_micro"], totals["recall_micro"]
    totals["f1_micro"] = round(2*p*r/(p+r), 4) if p is not None and r is not None and p+r else None
    totals["false_positive_per_10min"] = round(
        totals["false_positive"] * 10 / totals["scope_minutes"], 3) if totals["scope_minutes"] else None
    totals["estimated_gpu_usd"] = round(totals["estimated_gpu_usd"], 4) if cost_known else None
    return {
        "warning": "Only complete independent shot-attempt labels count; partially scanned games are prefix-only.",
        "totals": totals,
        "games": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Aggregate metrics for 1..100 labeled games")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--gpu-hourly-usd", type=float, default=None)
    args = parser.parse_args(argv)
    file = Path(args.manifest).resolve()
    manifest = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")
    report = batch_evaluate(manifest.get("games"), file.parent, args.gpu_hourly_usd)
    destination = Path(args.out)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "batch.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    totals = report["totals"]
    lines = [
        "# BasketCut 多场比赛基准评测",
        "",
        f"- 比赛：{totals['games']}，完成全片扫描：{totals['fully_scanned_games']}",
        f"- 人工投篮尝试：{totals['shots']}，候选：{totals['candidates']}",
        f"- 匹配：{totals['matches']}，漏检：{totals['missed']}，误报：{totals['false_positive']}",
        f"- Micro Precision / Recall / F1：{totals['precision_micro']} / {totals['recall_micro']} / {totals['f1_micro']}",
        f"- 每10分钟误报：{totals['false_positive_per_10min']}",
        f"- 估算 GPU 费用 USD：{totals['estimated_gpu_usd']}",
        "",
        "注意：这不是进球判定精度，且截断视频只评估已扫描前段。调参集与盲测集必须分开。",
        "",
    ]
    (destination / "batch.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
