"""V0.7 report human verification coverage without invoking a model.

Legacy data without recorded second inspection is counted as UNVERIFIED.
This program never fabricates accuracy, bounding boxes, labels or evidence.
"""
import argparse
import json
from pathlib import Path


def review_readiness(manifest):
    if not isinstance(manifest,dict) or not isinstance(manifest.get("samples"),list):
        raise ValueError("Expected training dataset manifest with samples")
    samples=manifest["samples"]
    games=manifest.get("games",[])
    if not isinstance(games,list):
        raise ValueError("Expected games list")
    positive=sum(row.get("ball_absent") is False for row in samples)
    negative=sum(row.get("ball_absent") is True for row in samples)
    reviewed=sum(row.get("quality_verified") is True and
                 row.get("quality_verified_by_id") is not None for row in samples)
    absent=sum(row.get("ball_absent") not in (True,False) for row in samples)
    by_reason={}
    for row in samples:
        if row.get("ball_absent") is True:
            reason=row.get("hard_negative_reason") or "unspecified"
            by_reason[reason]=by_reason.get(reason,0)+1
    checks={
        "all_labels_explicit_boolean":absent==0,
        "all_labels_second_pass_verified":len(samples)>0 and reviewed==len(samples),
        "at_least_8_distinct_games":len({str(g.get("id")) for g in games})>=8,
        "at_least_120_positive_frames":positive>=120,
        "at_least_40_explicit_negative_frames":negative>=40,
        "sealed_test_split":manifest.get("test_sealed") is True,
        "verified_export":manifest.get("label_review_required") is True,
        "not_synthetic":manifest.get("synthetic") is not True,
    }
    return {
        "schema_version":1,"scope":"V0.7 DATA READINESS ONLY",
        "games":len(games),"frames":len(samples),
        "positive_frames":positive,"explicit_negative_frames":negative,
        "verified_frames":reviewed,"unverified_frames":len(samples)-reviewed,
        "hard_negative_categories":by_reason,
        "checks":checks,
        "eligible_for_independent_model_benchmark":all(checks.values()),
        "production_release":False,
        "warning":"Dataset readiness is NOT basketball detection accuracy, shot detection, or proof of human visual review. Verification is a recorded user attestation.",
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Report real human basketball data readiness")
    p.add_argument("--manifest",required=True)
    p.add_argument("--out",required=True)
    args=p.parse_args(argv)
    content=json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    report=review_readiness(content)
    dst=Path(args.out)
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
