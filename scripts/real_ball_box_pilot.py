"""Build a tiny, genuinely HUMAN REVIEWED real-video box dataset for CPU smoke tests.

Source clip downloads are handled by Actions; no real images are committed.
Four manual frame boxes are provided under benchmarks/real_ball_boxes_pilot.json.
This is a pilot of the mechanics, not evidence of accuracy.
"""
import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path


def create_pilot(annotations, video_directory, destination):
    notes=json.loads(Path(annotations).read_text(encoding="utf-8"))
    if notes.get("schema_version")!=1 or len(notes.get("games",[]))<2:
        raise ValueError("Invalid pilot annotation manifest")
    root=Path(destination);root.mkdir(parents=True,exist_ok=True)
    samples=[];sources=[]
    used_games=set()
    for game in notes["games"]:
        game_id=int(game["id"]);split=game["split"]
        if game_id in used_games or split not in ("train","val"):
            raise ValueError("Invalid or duplicate game identity")
        used_games.add(game_id)
        if "CC0" not in game["rights"] and "CC BY-SA" not in game["rights"]:
            raise ValueError("Expected explicit source license")
        video=Path(video_directory)/(game["source_name"]+".webm")
        if not video.is_file():raise ValueError(f"Missing licensed source clip: {video}")
        sources.append({"id":game_id,"split":split,
                        "source_url":game["source_url"],"training_rights":game["rights"]})
        for index,item in enumerate(game["frames"]):
            x1,y1,x2,y2=item["box_xyxy_pixels"]
            if not (0<=x1<x2<=1280 and 0<=y1<y2<=720):
                raise ValueError("Human bbox outside source resolution")
            t=float(item["time_seconds"])
            if not math.isfinite(t) or t<0:raise ValueError("Invalid time")
            key=f"game{game_id}_{index}_{int(t*100):04d}"
            image=f"images/{split}/{key}.jpg"
            label=f"labels/{split}/{key}.txt"
            for sub in ("images","labels"):(root/sub/split).mkdir(parents=True,exist_ok=True)
            p=subprocess.run(["ffmpeg","-nostdin","-loglevel","error","-y",
                              "-ss",f"{t:.3f}","-i",str(video),"-frames:v","1",
                              "-vf","scale=1280:720","-q:v","2",str(root/image)],
                             capture_output=True,text=True,timeout=50)
            if p.returncode or not (root/image).exists():
                raise ValueError("Failed to extract a real frame: "+p.stderr[-250:])
            cx=(x1+x2)/2/1280;cy=(y1+y2)/2/720
            w=(x2-x1)/1280;h=(y2-y1)/720
            (root/label).write_text(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n")
            samples.append({"game_id":game_id,"split":split,"image":image,
                            "label":label,"ball_absent":False,
                            "manual_bbox_xyxy_pixel":[x1,y1,x2,y2],
                            "sha256_image":hashlib.sha256((root/image).read_bytes()).hexdigest()})
    if not ({x["split"] for x in samples}=={"train","val"}):
        raise ValueError("Need game-separated train and validation examples")
    manifest={"schema_version":1,"classes":["basketball"],
              "WARNING":"Real manually reviewed bbox frames, only FOUR. Not representative production accuracy.",
              "games":sources,"samples":samples}
    (root/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return manifest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--annotations",required=True)
    parser.add_argument("--videos",required=True)
    parser.add_argument("--out",required=True)
    args=parser.parse_args()
    result=create_pilot(args.annotations,args.videos,args.out)
    print(f"Real, reviewed positive bbox images: {len(result['samples'])} in {len(result['games'])} distinct games")


if __name__=="__main__":main()
