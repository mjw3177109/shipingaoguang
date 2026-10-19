"""Fine-tune torchvision MobileNetV3 Faster R-CNN on opt-in human basketball boxes.

CPU is supported (slow); GPU is optional. Outputs are never called 'shot detection'.
The val split is already disjoint by game_id in manifest.json.
"""
import argparse
import json
import random
import time
from pathlib import Path

from .specialist import box_counts, box_summary


def _safe_input(root, name):
    candidate=(root/name).resolve()
    if root!=candidate and root not in candidate.parents:
        raise ValueError("Dataset path escapes its root")
    if not candidate.is_file():
        raise ValueError(f"Missing dataset file {name}")
    return candidate


def train(dataset, output, *, epochs=1, max_steps=50, device="cpu", pretrained=True):
    import torch
    from PIL import Image
    import torchvision.transforms.functional as F
    from torchvision.models.detection import (
        fasterrcnn_mobilenet_v3_large_320_fpn,
        FasterRCNN_MobileNet_V3_Large_320_FPN_Weights)
    from torchvision.models.detection.faster_rcnn import FastRCNNPredictor

    root=Path(dataset).resolve()
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if manifest.get("classes")!=["basketball"] or manifest.get("schema_version")!=1:
        raise ValueError("Invalid one-class basketball dataset manifest")
    rows=manifest.get("samples",[])
    train_rows=[x for x in rows if x["split"]=="train"]
    val_rows=[x for x in rows if x["split"]=="val"]
    train_ids={x["game_id"] for x in train_rows};val_ids={x["game_id"] for x in val_rows}
    if not train_rows or not val_rows or train_ids & val_ids:
        raise ValueError("Train/validation must contain disjoint labeled games")
    if not 1<=epochs<=25 or not 1<=max_steps<=1000:
        raise ValueError("Training budget out of bounds")
    if device not in ("cpu","cuda") or (device=="cuda" and not torch.cuda.is_available()):
        raise ValueError("Requested device unavailable")
    torch.manual_seed(31); random.seed(31)
    if device=="cpu":torch.set_num_threads(min(4,torch.get_num_threads()))
    weights=FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT if pretrained else None
    model=fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights,weights_backbone=None)
    model.roi_heads.box_predictor=FastRCNNPredictor(
        model.roi_heads.box_predictor.cls_score.in_features,2)
    # Fast/cheap CPU fine-tune: freeze pretrained backbone, train detection heads.
    for param in model.backbone.parameters():param.requires_grad=False
    model.to(device)
    optimizer=torch.optim.SGD((p for p in model.parameters() if p.requires_grad),
                               lr=.001,momentum=.9,weight_decay=.0001)
    def load_sample(item):
        image_path=_safe_input(root,item["image"])
        label_path=_safe_input(root,item["label"])
        image=F.to_tensor(Image.open(image_path).convert("RGB"))
        _,h,w=image.shape
        boxes=[]
        for line in label_path.read_text().splitlines():
            parts=line.split()
            if len(parts)!=5 or parts[0]!="0":raise ValueError("Invalid YOLO basketball class")
            cx,cy,bw,bh=map(float,parts[1:])
            if not (0<=cx<=1 and 0<=cy<=1 and 0<bw<=1 and 0<bh<=1):
                raise ValueError("Invalid normalized bbox")
            boxes.append([(cx-bw/2)*w,(cy-bh/2)*h,
                          (cx+bw/2)*w,(cy+bh/2)*h])
        target={"boxes":torch.tensor(boxes,dtype=torch.float32).reshape(-1,4).to(device),
                "labels":torch.ones(len(boxes),dtype=torch.int64,device=device)}
        return image.to(device),target
    started=time.monotonic()
    losses=[]
    for epoch in range(epochs):
        model.train()
        indices=list(range(len(train_rows)))
        random.Random(50+epoch).shuffle(indices)
        for idx in indices[:max_steps]:
            image,target=load_sample(train_rows[idx])
            batch=model([image],[target])
            loss=sum(batch.values())
            if not torch.isfinite(loss).item():raise ValueError("Non-finite training loss")
            optimizer.zero_grad();loss.backward();optimizer.step()
            losses.append(round(float(loss.detach().cpu()),5))
    destination=Path(output);destination.parent.mkdir(parents=True,exist_ok=True)
    # Record provenance, split, one-class head and inference architecture.
    torch.save({"state_dict":model.state_dict(),"classes":["background","basketball"],
                "architecture":"mobile320_binary_v1",
                "training_game_ids":sorted(train_ids),
                "validation_game_ids":sorted(val_ids)},
               destination)
    totals={"tp":0,"fp":0,"fn":0}
    model.eval()
    with torch.inference_mode():
        for item in val_rows:
            image,target=load_sample(item)
            prediction=model([image])[0]
            good=prediction["scores"]>=.25
            boxes=prediction["boxes"][good].cpu().tolist()
            truth=target["boxes"].cpu().tolist()
            counts=box_counts(boxes,truth,threshold=.5)
            for key in totals:totals[key]+=counts[key]
    summary={
        "schema_version":1,
        "warning":"Only basketball bounding-box metrics. NEVER infer made shots or highlight recall.",
        "data_source":"explicitly approved human video-frame annotations",
        "train_games":len(train_ids),"val_games":len(val_ids),
        "train_images":len(train_rows),"val_images":len(val_rows),
        "epochs":epochs,"steps":len(losses),"device":device,
        "pretrained_coco_weights":bool(pretrained),
        "last_loss":losses[-1] if losses else None,
        "box_metrics_iou_0_5_conf_0_25":box_summary(totals),
        "elapsed_seconds":round(time.monotonic()-started,2),
        "checkpoint":str(destination),
    }
    report=destination.with_suffix(".report.json")
    report.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description="CPU / optional GPU train one-class basketball box detector")
    p.add_argument("--dataset",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--epochs",type=int,default=1)
    p.add_argument("--max-steps",type=int,default=50)
    p.add_argument("--device",choices=("cpu","cuda"),default="cpu")
    p.add_argument("--no-pretrained",action="store_true")
    args=p.parse_args(argv)
    print(json.dumps(train(args.dataset,args.out,epochs=args.epochs,
                 max_steps=args.max_steps,device=args.device,
                 pretrained=not args.no_pretrained),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
