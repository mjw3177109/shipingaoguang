"""CPU-only inference on sealed test frames: COCO baseline or research specialist.

Predict into the shared normalized box JSON format consumed by compare_v05.
No video is exported here. Run both models on the *same* heldout images.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path


def predict(dataset, destination, *, model_kind, checkpoint=None, allow_research=False,
            confidence_floor=.05):
    if model_kind not in ("coco-mobile320", "specialist"):
        raise ValueError("Unknown model_kind")
    if model_kind=="specialist" and not checkpoint:
        raise ValueError("Specialist requires --checkpoint")
    if model_kind=="coco-mobile320" and checkpoint:
        raise ValueError("COCO baseline cannot use an external checkpoint")
    if not .01<=confidence_floor<=.5:
        raise ValueError("confidence_floor must be between .01 and .5")
    import torch
    from PIL import Image
    import torchvision.transforms.functional as F
    from torchvision.models.detection import (
        fasterrcnn_mobilenet_v3_large_320_fpn,
        FasterRCNN_MobileNet_V3_Large_320_FPN_Weights)
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    root=Path(dataset).resolve()
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if not (manifest.get("test_sealed") is True and
            manifest.get("method")=="three_way_sealed_test_v05"):
        raise ValueError("Requires game-disjoint sealed V0.5 test manifest")
    rows=[r for r in manifest["samples"] if r["split"]=="test"]
    if not rows:
        raise ValueError("No sealed test frames")
    if model_kind=="coco-mobile320":
        weights=FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
        model=fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights)
        chosen_class=weights.meta["categories"].index("sports ball")
    else:
        ckpt=torch.load(checkpoint,map_location="cpu",weights_only=True)
        if ckpt.get("research_only",True) and not allow_research:
            raise ValueError("Unapproved specialist checkpoint. Explicit --allow-research required.")
        if (ckpt.get("architecture")!="mobile320_binary_v1" or
                ckpt.get("classes")!=["background","basketball"]):
            raise ValueError("Wrong basketball specialist checkpoint format")
        if set(map(str,ckpt.get("training_game_ids",[]))) & {
                str(r["game_id"]) for r in rows}:
            raise ValueError("TEST GAME LEAKAGE: checkpoint training game appears in sealed test")
        manifest_hash=hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest()
        if ckpt.get("dataset_manifest_sha256") != manifest_hash:
            raise ValueError("Checkpoint dataset manifest SHA256 differs: training provenance mismatch")
        actual_test_ids={str(r["game_id"]) for r in rows}
        if set(map(str,ckpt.get("sealed_test_game_ids",[]))) != actual_test_ids:
            raise ValueError("Checkpoint sealed test game IDs differ from this dataset")
        if set(map(str,ckpt.get("validation_game_ids",[]))) & actual_test_ids:
            raise ValueError("TEST GAME LEAKAGE: checkpoint validation game in sealed test")
        model=fasterrcnn_mobilenet_v3_large_320_fpn(weights=None,weights_backbone=None)
        model.roi_heads.box_predictor=FastRCNNPredictor(
            model.roi_heads.box_predictor.cls_score.in_features,2)
        model.load_state_dict(ckpt["state_dict"],strict=True)
        chosen_class=1
    model.eval().to("cpu")
    torch.set_num_threads(min(4,torch.get_num_threads()))
    predictions={}
    with torch.inference_mode():
        for row in rows:
            name=row["image"]
            path=(root/name).resolve()
            if root not in path.parents or not path.is_file():
                raise ValueError("Invalid test image path")
            image=F.to_tensor(Image.open(path).convert("RGB"))
            _,height,width=image.shape
            prediction=model([image])[0]
            detections=[]
            for coords,label,score in zip(
                    prediction["boxes"],prediction["labels"],prediction["scores"]):
                q=float(score)
                if int(label)!=chosen_class or q<confidence_floor:
                    continue
                x1,y1,x2,y2=[float(v) for v in coords]
                # Clip model output to image bounds (remove zero-size results).
                box=[max(0,min(1,x1/width)), max(0,min(1,y1/height)),
                     max(0,min(1,x2/width)), max(0,min(1,y2/height))]
                if not box[0]<box[2] or not box[1]<box[3]:
                    continue
                detections.append({"bbox":[round(x,6) for x in box],
                                   "score":round(q,5)})
            predictions[name]=detections[:300]
    output={"schema_version":1,"model_kind":model_kind,
            "research_only":True,"test_game_ids":sorted(
                {str(row["game_id"]) for row in rows}),
            "predictions":predictions,
            "warning":"Research predictions only; no event classification or model release."}
    target=Path(destination);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    return output


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--dataset",required=True)
    parser.add_argument("--out",required=True)
    parser.add_argument("--model",choices=("coco-mobile320","specialist"),required=True)
    parser.add_argument("--checkpoint")
    parser.add_argument("--allow-research",action="store_true")
    args=parser.parse_args(argv)
    report=predict(args.dataset,args.out,model_kind=args.model,
                   checkpoint=args.checkpoint,allow_research=args.allow_research)
    print(f"Evaluated {len(report['predictions'])} TEST frames using CPU {args.model}; research-only")


if __name__=="__main__":
    main()
