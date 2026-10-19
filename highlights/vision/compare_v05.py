"""Same-game, same-image benchmark for baseline and challenger detections.

Prediction JSON: {"schema_version": 1, "predictions":
   {"images/test/game3_t...jpg":[{"bbox":[x1,y1,x2,y2],"score":.8}]}}
Normalized coordinates are 0..1. Every TEST image must be explicitly supplied;
no missing-image-as-empty loophole. The training set is never evaluated here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from .specialist import box_counts, box_summary
from .split_v05 import audit_three_way


def _safe_file(root, relative):
    p = (root / relative).resolve()
    if root not in p.parents or not p.is_file():
        raise ValueError("Dataset file missing or outside root")
    return p


def _read_truth(root, rows):
    result = {}
    for row in rows:
        label = _safe_file(root, row["label"])
        truth=[]
        for line in label.read_text(encoding="utf-8").splitlines():
            fields=line.split()
            if len(fields)!=5 or fields[0]!="0":
                raise ValueError(f"Invalid basketball label in {row['label']}")
            nums=list(map(float,fields[1:]))
            if not all(math.isfinite(x) for x in nums):
                raise ValueError("Nonfinite basketball label")
            cx,cy,w,h=nums
            if not 0 < w <= 1 or not 0 < h <= 1 or not (0 <= cx-w/2 < cx+w/2 <= 1 and 0 <= cy-h/2 < cy+h/2 <= 1):
                raise ValueError("Out-of-bounds YOLO label")
            truth.append([cx-w/2,cy-h/2,cx+w/2,cy+h/2])
        if bool(row["ball_absent"]) != (len(truth)==0):
            raise ValueError("Explicit negative / YOLO labels disagree")
        if row["image"] in result:
            raise ValueError("Duplicate frame path")
        result[row["image"]] = truth
    return result


def _read_predictions(path, truth_keys, *, threshold=.25):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version")!=1 or not isinstance(data.get("predictions"),dict):
        raise ValueError("Predictions must use schema_version=1 and predictions mapping")
    mapping=data["predictions"]
    if set(mapping) != set(truth_keys):
        raise ValueError(f"Prediction coverage mismatch: missing={len(set(truth_keys)-set(mapping))}, extra={len(set(mapping)-set(truth_keys))}")
    result={}
    for name,predictions in mapping.items():
        if not isinstance(predictions,list) or len(predictions)>300:
            raise ValueError("Each image requires a bounded detection list")
        boxes=[]
        for p in predictions:
            if not isinstance(p,dict) or not isinstance(p.get("bbox"),list) or len(p["bbox"])!=4:
                raise ValueError("Invalid prediction bbox")
            box=p["bbox"]
            if any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in box):
                raise ValueError("Invalid nonfinite prediction coordinate")
            x1,y1,x2,y2=box
            if not (0<=x1<x2<=1 and 0<=y1<y2<=1):
                raise ValueError("Prediction bbox outside normalized image")
            score=p.get("score")
            if (isinstance(score,bool) or not isinstance(score,(float,int)) or
                    not math.isfinite(score) or not 0<=score<=1):
                raise ValueError("Invalid confidence")
            if score>=threshold:
                boxes.append(box)
        result[name]=boxes
    return result


def _metrics(predictions, truth):
    total={"tp":0,"fp":0,"fn":0}
    negative_fp=0
    for name, boxes in predictions.items():
        counts=box_counts(boxes,truth[name],threshold=.5)
        for k in total:total[k]+=counts[k]
        if not truth[name]:
            negative_fp+=len(boxes)
    data=box_summary(total)
    negative=sum(not value for value in truth.values())
    data["negative_frames"]=negative
    data["false_boxes_on_negative_frames"]=negative_fp
    data["false_boxes_per_100_negative_frames"]=(
        round(100*negative_fp/negative,3) if negative else None)
    data["evaluated_images"]=len(truth)
    return data


def compare_dataset(dataset, baseline_path, challenger_path, *,
                    min_precision=.75, min_recall=.7, max_negative_fp_per_100=15,
                    min_f1_improvement=.03):
    root=Path(dataset).resolve()
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version")!=1 or manifest.get("classes")!=["basketball"]:
        raise ValueError("Invalid basketball dataset")
    games=manifest.get("games",[])
    rows=manifest.get("samples",[])
    audit=audit_three_way(games)
    # Check game-disjointness independently of any self-reported audit.
    splits={str(x["id"]):x["split"] for x in games}
    if set(splits.values())!={"train","val","test"}:
        raise ValueError("Missing independent train/val/test split")
    sample_stats={game_id:{"positive":0,"negative":0} for game_id in splits}
    for row in rows:
        owner=str(row["game_id"])
        if row["split"] != splits.get(owner):
            raise ValueError("Frame crosses game split boundary")
        if type(row.get("ball_absent")) is not bool:
            raise ValueError("Frame ball_absent must be an explicit boolean")
        sample_stats[owner]["negative" if row["ball_absent"] else "positive"]+=1
    for game in games:
        stats=sample_stats[str(game["id"])]
        if (stats["positive"]!=game["positive"] or
                stats["negative"]!=game["negative"] or
                stats["positive"]+stats["negative"]!=game.get("sample_count",
                     stats["positive"]+stats["negative"])):
            raise ValueError("Manifest game counts do not match actual labeled samples")
    heldout=[r for r in rows if r["split"]=="test"]
    if not heldout:
        raise ValueError("No sealed test frames")
    truth=_read_truth(root,heldout)
    # Cross-split image hash collisions or duplicate test images require manual review.
    sha_to_splits={}
    for row in rows:
        # Verify manifest hashes against file bytes; never trust a self-declared
        # SHA when deciding whether the sealed test has been leaked.
        digest=row.get("sha256_image")
        if not isinstance(digest,str) or len(digest)!=64:
            raise ValueError("Every sample requires SHA256 image provenance")
        actual=hashlib.sha256(_safe_file(root,row["image"]).read_bytes()).hexdigest()
        if actual!=digest:
            raise ValueError("Image SHA256 mismatch - possible dataset tampering")

        if digest:
            sha_to_splits.setdefault(digest,set()).add(row["split"])
    duplicate_cross_split=sum(len(v)>1 for v in sha_to_splits.values())
    b=_metrics(_read_predictions(baseline_path,truth),truth)
    c=_metrics(_read_predictions(challenger_path,truth),truth)
    f1_gain=round((c["f1"] or 0) -(b["f1"] or 0),4)
    checks={
        "non_synthetic_real_data": manifest.get("synthetic") is not True,
        "all_games_have_recorded_training_rights": all(
            isinstance(g.get("training_rights"),str) and
            len(g["training_rights"].strip())>=12 for g in games),
        "predictions_not_marked_synthetic": not (
            json.loads(Path(baseline_path).read_text()).get("synthetic") is True or
            json.loads(Path(challenger_path).read_text()).get("synthetic") is True),
        "v07_verified_labels": (
            manifest.get("label_review_required") is True and
            all(item.get("quality_verified") is True and
                item.get("quality_verified_by_id") is not None and
                item.get("quality_verified_at") is not None
                for item in rows)
        ),
        "data_ready":audit["data_coverage_ready"],
        "no_cross_split_identical_image_hashes":duplicate_cross_split==0,
        "enough_precision":c["precision"] is not None and c["precision"]>=min_precision,
        "enough_recall":c["recall"] is not None and c["recall"]>=min_recall,
        "negative_false_boxes_below_budget":(
            c["false_boxes_per_100_negative_frames"] is not None and
            c["false_boxes_per_100_negative_frames"]<=max_negative_fp_per_100),
        "improves_over_baseline":f1_gain>=min_f1_improvement,
    }
    return {
        "schema_version":1,"evaluation_scope":"SEALED_TEST_ONLY",
        "test_game_ids":sorted(g["id"] for g in games if g["split"]=="test"),
        "data_audit":audit,"baseline":b,"challenger":c,
        "f1_gain":f1_gain,"cross_split_duplicate_image_sha_count":duplicate_cross_split,
        "checks":checks,
        "eligible_for_independent_human_review":all(checks.values()),
        "production_release":False,
        "warning":"Metrics are basketball boxes, not made baskets. Pass never auto-promotes model: further human signoff + privacy/license review required.",
    }


def main(argv=None):
    p=argparse.ArgumentParser(description="Compare models only on independent human-labeled basketball test games")
    p.add_argument("--dataset",required=True)
    p.add_argument("--baseline",required=True)
    p.add_argument("--challenger",required=True)
    p.add_argument("--out",required=True)
    args=p.parse_args(argv)
    report=compare_dataset(args.dataset,args.baseline,args.challenger)
    path=Path(args.out);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"baseline":report["baseline"],"challenger":report["challenger"],
                      "eligible_for_human_review":report["eligible_for_independent_human_review"],
                      "production_release":report["production_release"]},
                     ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
