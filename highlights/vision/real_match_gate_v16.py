"""Strict, private full-game admission audit: attestations != independent verification.

This does not download, publish, or upload any private recording. The operator
supplies paths on an isolated machine. Existing candidate retrieval metrics are
reused; no model accuracy is invented.
"""
import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .evaluate import evaluate, validate_proposals, validate_truth


def media_details(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration", "-of", "json", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    return float(json.loads(result.stdout)["format"]["duration"])


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scoped_path(base_dir, path):
    if not isinstance(path, str) or not path:
        raise ValueError("Nonempty relative filename required")
    target = (base_dir / path).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        raise ValueError("Paths outside manifest folder forbidden")
    if not target.is_file():
        raise ValueError("Referenced evidence file missing")
    return target


def audit(manifest, base_dir, *, media_probe=media_details):
    checks = {}
    metrics = []
    def record(key, ok, reason):
        checks[key] = {"status": "PASS" if ok else "FAIL", "detail": reason}

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Expected V1.6 schema_version=1 manifest")
    entries = manifest.get("games")
    if not isinstance(entries, list):
        raise ValueError("games must be an array")
    record("at_least_three_distinct_games", len(entries) >= 3,
           "Minimum 3 independent, continuously recorded 60-120 minute matches")
    ids = [e.get("id") if isinstance(e, dict) else None for e in entries]
    record("unique_game_ids", len(set(str(x) for x in ids)) == len(ids)
           and all(isinstance(x, str) and x.strip() for x in ids),
           "Distinct nonblank game IDs are required")
    fingerprints = set()
    for idx, entry in enumerate(entries):
        key = f"game_{idx + 1}"
        try:
            if not isinstance(entry, dict):
                raise ValueError("game manifest entry must be an object")
            if entry.get("synthetic") is not False or entry.get("looped") is not False:
                raise ValueError("Synthetic, looped or undeclared source disallowed")
            if entry.get("continuous_unedited_full_match") is not True:
                raise ValueError("Continuous full-match operator attestation missing")
            if entry.get("rights_to_process_and_store") is not True:
                raise ValueError("Rights and participants' privacy consent not attested")
            if not isinstance(entry.get("license_evidence_reference"), str) or len(
                    entry["license_evidence_reference"].strip()) < 6:
                raise ValueError("Documented rights / authorization reference required")
            if entry.get("independent_of_training_and_tuning") is not True:
                raise ValueError("Holdout match cannot have been used for tuning or training")
            reviews = entry.get("human_reviewers")
            if not isinstance(reviews, list) or len(reviews) < 2:
                raise ValueError("Two independent human annotation attestations required")
            reviewers = {r.get("reviewer_id") for r in reviews if isinstance(r, dict)
                         and r.get("verified_complete_shot_attempt_labels") is True
                         and r.get("review_evidence_reference")}
            if len(reviewers) < 2 or None in reviewers:
                raise ValueError("Two different reviewers must attest exhaustive shot_attempt labels")
            source = scoped_path(base_dir, entry.get("video_file"))
            source_hash = sha256(source)
            claimed = entry.get("video_sha256")
            if not isinstance(claimed, str) or len(claimed) != 64 or claimed != source_hash:
                raise ValueError("Actual source bytes differ from declared SHA-256")
            if source_hash in fingerprints:
                raise ValueError("Duplicate recording content across separate game IDs")
            fingerprints.add(source_hash)
            duration = media_probe(source)
            if not isinstance(duration, (int, float)) or not math.isfinite(duration):
                raise ValueError("Invalid FFprobe duration")
            if not 3600 <= duration <= 7200:
                raise ValueError("Source media must be 60-120 minutes")
            proposal = json.loads(scoped_path(base_dir, entry.get("proposals_file")).read_text())
            truth = json.loads(scoped_path(base_dir, entry.get("truth_file")).read_text())
            if str(proposal.get("game_id")) != entry["id"] or str(truth.get("game_id")) != entry["id"]:
                raise ValueError("Ground truth/proposals must identify same game")
            td, events = validate_truth(truth)
            pd, clips, scanned, partial, analysis = validate_proposals(proposal)
            if abs(td - duration) > 3 or abs(pd - duration) > 3:
                raise ValueError("Ground truth/proposals and video duration mismatch")
            if partial or scanned < duration - .05 or analysis.get("coverage_ratio", 0) < .999:
                raise ValueError("CPU analysis did not cover the entire game")
            if len(events) < 20:
                raise ValueError("Fewer than 20 independently labeled shot attempts")
            result = evaluate(proposal, truth)
            if result["scope"]["full_video"] is not True:
                raise ValueError("Evaluation only covered part of video")
            metrics.append({
                "id": entry["id"],
                "duration_seconds": round(duration, 3),
                "annotated_attempts": len(events),
                "candidate_clips": len(clips),
                "precision": result["performance"]["precision"],
                "recall": result["performance"]["recall"],
                "f1": result["performance"]["f1"],
                "true_matches": result["performance"]["true_positive_matches"],
                "false_positive_clips": result["performance"]["false_positive_clips"],
                "missed_shots": result["performance"]["missed_shots"],
                "source_sha256": source_hash,
                "claim_type": "operator_and_reviewer_attested_not_independently_verified",
            })
            record(key, True, "Video, rights attestations, full labels and scan pass software gates")
        except (KeyError, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            # Fail closed; do not print private paths or raw video labels.
            record(key, False, f"Evidence rejected: {type(exc).__name__}. Inspect private input locally.")
    eligible = len(entries) >= 3 and all(v["status"] == "PASS" for v in checks.values())
    tp = sum(m["true_matches"] for m in metrics)
    preds = sum(m["candidate_clips"] for m in metrics)
    truth_total = sum(m["annotated_attempts"] for m in metrics)
    p = tp / preds if preds else None
    r = tp / truth_total if truth_total else None
    f1 = 2*p*r/(p+r) if p is not None and r is not None and p+r else None
    return {
        "schema_version": 1, "basketcut_version": "1.6",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "outcome": "ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW" if eligible else "BLOCKED",
        "independent_source_and_rights_validation_completed": False,
        "basketball_made_shot_accuracy_measured": False,
        "scope": "shot_attempt candidate retrieval only; made baskets not classified",
        "checks": checks,
        "games": metrics,
        "aggregate": {
            "eligible_game_records": len(metrics),
            "annotated_shot_attempts": truth_total,
            "candidate_clips": preds,
            "matches": tp,
            "precision_micro": round(p, 4) if p is not None else None,
            "recall_micro": round(r, 4) if r is not None else None,
            "f1_micro": round(f1, 4) if f1 is not None else None,
        },
        "limitations": [
            "All licensing, continuity and reviewer fields are operator-supplied attestations",
            "Software check cannot establish an unedited real game or lawful consent",
            "Even eligible reports require separate independent external review",
            "Metrics are retrieval of shot attempts, NOT scored basket classification",
            "Real full-game footage is deliberately never included in report artifacts",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    source = Path(args.manifest).resolve()
    result = audit(json.loads(source.read_text(encoding="utf-8")), source.parent)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"outcome": result["outcome"], "checks": result["checks"],
                      "aggregate": result["aggregate"]}, ensure_ascii=False, indent=2))
    if args.strict and result["outcome"] != "ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
