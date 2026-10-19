"""Synthetic mechanical training smoke ONLY; not a basketball accuracy benchmark.

Creates toy colored circles as stand-ins. Real model training requires approved
human-box labels exported from Django. Designed for low-cost CPU Actions.
"""
import argparse
import json
from pathlib import Path

def generate(path):
    from PIL import Image, ImageDraw
    root=Path(path);root.mkdir(parents=True,exist_ok=True)
    samples=[]
    for game_id,split in ((1001,"train"),(1002,"val")):
        for i in range(2):
            dir_im=root/"images"/split;dir_lb=root/"labels"/split
            dir_im.mkdir(parents=True,exist_ok=True);dir_lb.mkdir(parents=True,exist_ok=True)
            key=f"toy{game_id}_{i}"
            image=Image.new("RGB",(96,96),(40,50,70))
            absent=bool(i)
            if not absent:
                ImageDraw.Draw(image).ellipse((40,28,56,44),fill=(220,100,25))
            image.save(dir_im/(key+".jpg"))
            (dir_lb/(key+".txt")).write_text(
                "0 0.5 0.375 0.166667 0.166667\n" if not absent else "")
            samples.append({"game_id":game_id,"split":split,
                "image":f"images/{split}/{key}.jpg",
                "label":f"labels/{split}/{key}.txt",
                "ball_absent":absent})
    (root/"manifest.json").write_text(json.dumps({
       "schema_version":1,"classes":["basketball"],
       "WARNING":"SYNTHETIC TOY FIXTURE - ZERO REAL DETECTION EVIDENCE",
       "samples":samples},indent=2))
    return root

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True)
    p.add_argument("--checkpoint",required=True)
    a=p.parse_args()
    generate(a.out)
    from highlights.vision.train_specialist import train
    result=train(a.out,a.checkpoint,epochs=1,max_steps=1,pretrained=False)
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
