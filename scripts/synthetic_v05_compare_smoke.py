"""SYNTHETIC verification only: data separation, scoring and release gates.

Images are *fake bytes* for the stdlib metrics harness; NOT actual JPEGs.
Never use this fixture for model inference or advertise its toy scores.
"""
import argparse
import hashlib
import json
from pathlib import Path

from highlights.vision.split_v05 import three_way_games


def create_fixture(root, *, game_count=8, positives=20, negatives=10,
                   synthetic=True):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    split=three_way_games(list(range(1001,1001+game_count)))
    games=[];samples=[]
    baseline={};challenger={}
    for game in sorted(split):
        label=split[game]
        games.append({"id":game,"split":label,"positive":positives,
                      "negative":negatives, "training_rights":"SYNTHETIC FIXTURE ONLY"})
        for index in range(positives+negatives):
            key=f"g{game}-frame-{index:04d}"
            image=f"images/{label}/{key}.jpg"
            annotation=f"labels/{label}/{key}.txt"
            (root/image).parent.mkdir(parents=True,exist_ok=True)
            (root/annotation).parent.mkdir(parents=True,exist_ok=True)
            # Deliberately NOT an image. This fixture tests metrics logic only.
            raw=(f"TOY NOT JPEG {game} {index}\n").encode()
            (root/image).write_bytes(raw)
            present=index<positives
            (root/annotation).write_text(
                "0 0.500000 0.500000 0.200000 0.200000\n" if present else "",encoding="utf-8")
            samples.append({"game_id":game,"split":label,"image":image,
                            "label":annotation,"ball_absent":not present,
                            "sha256_image":hashlib.sha256(raw).hexdigest()})
            if label=="test":
                baseline[image]=[]
                challenger[image]=[{"bbox":[.4,.4,.6,.6],"score":.95}] if present else []
    manifest={"schema_version":1,"classes":["basketball"],"test_sealed":True,
              "method":"three_way_sealed_test_v05","synthetic":synthetic,
              "warning":"SYNTHETIC METRICS ONLY: NOT MODEL PERFORMANCE",
              "games":games,"samples":samples}
    (root/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    for name,data in (("baseline",baseline),("challenger",challenger)):
        (root/(name+".json")).write_text(
            json.dumps({"schema_version":1,"synthetic":True,
                        "predictions":data},indent=2),encoding="utf-8")
    return root


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    args=p.parse_args()
    root=create_fixture(args.out)
    from highlights.vision.compare_v05 import compare_dataset
    report=compare_dataset(root,root/"baseline.json",root/"challenger.json")
    assert report["baseline"]["recall"]==0.
    assert report["challenger"]["recall"]==1.
    assert report["checks"]["non_synthetic_real_data"] is False
    assert report["eligible_for_independent_human_review"] is False
    assert report["production_release"] is False
    (root/"toy_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print("PASS: SYNTHETIC same-frame comparison, no training leakage, no auto-release")


if __name__=="__main__":
    main()
