"""Export consented owner-scoped basketball boxes as YOLO one-class dataset.

Train/validation split is by GAME, never by adjacent frames. No media is
published automatically. Invoke with --owner-id explicitly.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from highlights.ball_labels import validate_ball_label
from highlights.ball_views import extract_frame_jpeg
from highlights.models import Game
from highlights.vision.data_quality import audit_games
from highlights.vision.split_v05 import three_way_games, audit_three_way


def build_dataset(owner_id, directory, game_ids=None, val_ratio=.25, max_frames=200, *, three_way=False, test_ratio=.20):
    if not 0 < val_ratio <= .5 or max_frames < 1 or max_frames > 3000:
        raise ValueError("Invalid split or frame limit")
    get_user_model().objects.get(pk=owner_id)
    qs=Game.objects.filter(venue__owner_id=owner_id, ball_training_approved=True).order_by("pk")
    if game_ids:
        qs=qs.filter(pk__in=game_ids)
    games=[g for g in qs if g.ball_frames.exists() and len(g.ball_training_rights.strip())>=12]
    if len(games)<(6 if three_way else 2):
        raise ValueError("Require at least six approved games for train/val/test, or two for legacy train/val")
    if game_ids and set(map(int,game_ids)) != {g.pk for g in games}:
        raise ValueError("Some selected games are not labeled, consented or owned by the selected user")
    root=Path(directory).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Output directory must be empty to prevent stale samples or data leakage")
    root.mkdir(parents=True, exist_ok=True)
    games=sorted(games,key=lambda g: hashlib.sha256(f"basketcut-game-{g.pk}".encode()).hexdigest())
    if three_way:
        split_by_game=three_way_games([g.pk for g in games], val_ratio, test_ratio)
    else:
        val_count=max(2 if len(games)>=4 else 1, min(len(games)-1, math.ceil(len(games)*val_ratio)))
        val_ids={g.pk for g in games[:val_count]}
        split_by_game={g.pk:("val" if g.pk in val_ids else "train") for g in games}
    samples=[]
    games_info=[]
    for game in games:
        split=split_by_game[game.pk]
        labels=list(game.ball_frames.all()[:max_frames])
        games_info.append({"id":game.pk,"split":split,"sample_count":len(labels),
                           "positive":sum(not x.ball_absent for x in labels),
                           "negative":sum(x.ball_absent for x in labels),
                           "training_rights":game.ball_training_rights})
        image_dir=root/"images"/split
        label_dir=root/"labels"/split
        image_dir.mkdir(parents=True,exist_ok=True)
        label_dir.mkdir(parents=True,exist_ok=True)
        for item in labels:
            moment, rect=validate_ball_label(
                item.time_seconds,{"x":item.x,"y":item.y,"w":item.w,"h":item.h},
                game.duration_seconds, absent=item.ball_absent)
            key=f"game{game.pk}_t{round(moment*100):09d}"
            image=f"images/{split}/{key}.jpg"
            label=f"labels/{split}/{key}.txt"
            raw=extract_frame_jpeg(game,moment)
            (root/image).write_bytes(raw)
            if item.ball_absent:
                yolo=""
            else:
                x,y,w,h=rect
                yolo=f"0 {x+w/2:.6f} {y+h/2:.6f} {w:.6f} {h:.6f}\n"
            (root/label).write_text(yolo,encoding="utf-8")
            samples.append({"game_id":game.pk,"split":split,
                            "time_seconds":moment,"image":image,"label":label,
                            "ball_absent":bool(item.ball_absent),
                            "bbox_xywh_norm":list(rect),
                            "sha256_image":hashlib.sha256(raw).hexdigest()})
    if not any(x["split"]=="train" and not x["ball_absent"] for x in samples):
        raise ValueError("Training split contains no positive basketball examples")
    if not any(x["split"]=="val" for x in samples):
        raise ValueError("Validation split is empty")
    audit = audit_three_way(games_info) if three_way else audit_games(games_info)
    manifest={"schema_version":1,"classes":["basketball"],"owner_id":str(owner_id),
              "quality_audit":audit,
              "method":"three_way_sealed_test_v05" if three_way else "game-disjoint deterministic SHA256 split",
              "test_sealed":bool(three_way),
              "warning":"Annotations must be human verified; no claimed accuracy or automatic score.",
              "games":games_info,"samples":samples}
    (root/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"quality_report.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"data.yaml").write_text(
        f"path: {root.as_posix()}\ntrain: images/train\nval: images/val\n"+
        ("test: images/test\n" if three_way else "") + "names:\n  0: basketball\n",
        encoding="utf-8")
    return manifest


class Command(BaseCommand):
    help="Export manually verified and explicitly consented basketball bounding boxes"
    def add_arguments(self,parser):
        parser.add_argument("--owner-id",type=int,required=True)
        parser.add_argument("--out",required=True)
        parser.add_argument("--game-ids",nargs="+",type=int)
        parser.add_argument("--val-ratio",type=float,default=.25)
        parser.add_argument("--three-way",action="store_true", help="Independent GAME heldout test set, requires at least 6 games")
        parser.add_argument("--test-ratio",type=float,default=.20)
    def handle(self,*args,**opts):
        try:
            data=build_dataset(opts["owner_id"],opts["out"],opts["game_ids"],opts["val_ratio"],
                               three_way=opts["three_way"],test_ratio=opts["test_ratio"])
        except (ValueError,KeyError, get_user_model().DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Exported {len(data['samples'])} human-labeled images across {len(data['games'])} games"))
