"""V0.3 tests: tenant isolation, human label lifecycle and game-disjoint export."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .ball_labels import validate_ball_label
from .management.commands.export_ball_dataset import build_dataset
from .models import BallFrameAnnotation, Game, Venue
from .vision.specialist import box_counts, box_summary, iou_xyxy


class BallBoxPureTests(TestCase):
    def test_reject_nan_outside_and_tiny_boxes(self):
        for bad in ({"x":float("nan"),"y":.1,"w":.02,"h":.02},
                    {"x":.99,"y":.2,"w":.1,"h":.1},
                    {"x":.4,"y":.2,"w":.001,"h":.02}):
            with self.assertRaises(ValueError):
                validate_ball_label(1,bad,3)
        self.assertEqual(validate_ball_label(1,None,3,absent=True),(1,(0.,0.,0.,0.)))

    def test_box_metrics_count_false_positives(self):
        ground=[[10,10,30,30]]
        predictions=[[10,10,30,30],[60,60,80,80]]
        count=box_counts(predictions,ground)
        self.assertEqual(count,{"tp":1,"fp":1,"fn":0})
        self.assertEqual(box_summary(count)["precision"],.5)
        self.assertEqual(iou_xyxy([0,0,1,1],[5,5,6,6]),0)


class BallLabelWebTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.mkdtemp()
        ov=override_settings(MEDIA_ROOT=self.tmp)
        ov.enable()
        self.addCleanup(ov.disable)
        self.addCleanup(shutil.rmtree,self.tmp,ignore_errors=True)
        users=get_user_model()
        self.owner=users.objects.create_user(username="boxowner",password="safePW1x")
        self.other=users.objects.create_user(username="boxother",password="safePW1x")
        self.v=Venue.objects.create(owner=self.owner,name="TestGym")
        self.v_other=Venue.objects.create(owner=self.other,name="OtherGym")
        def mk_game(venue,title):
            return Game.objects.create(
                venue=venue,title=title,duration_seconds=3,
                video=SimpleUploadedFile(f"{title}.mp4",b"dummy"))
        self.game=mk_game(self.v,"game1")
        self.game2=mk_game(self.v,"game2")
        self.other_game=mk_game(self.v_other,"othergame")

    def _save(self,game,time="1.2",absent=False):
        values={"time_seconds":time}
        if absent:values["ball_absent"]="yes"
        else:values.update({"x":".2","y":".25","w":".04","h":".05"})
        return self.client.post(reverse("save_ball_label",args=[game.pk]),values)

    def test_private_frames_and_annotations(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("ball_labels",args=[self.game.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse("ball_frame_jpg",args=[self.game.pk])+"?t=0").status_code,404)
        self.assertEqual(self._save(self.game).status_code,404)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("ball_labels",args=[self.game.pk])), "ball-canvas")
        self.assertEqual(self._save(self.game).status_code,302)
        self.assertEqual(BallFrameAnnotation.objects.filter(game=self.game).count(),1)
        self.assertEqual(self._save(self.game,absent=True).status_code,302)
        self.assertEqual(BallFrameAnnotation.objects.filter(game=self.game).count(),1)
        self.assertTrue(self.game.ball_frames.first().ball_absent)
        self.assertEqual(self._save(self.game,time="notanumber").status_code,302)
        self.assertEqual(BallFrameAnnotation.objects.filter(game=self.game).count(),1)

    def test_negative_reason_requires_human_absence_and_is_not_a_model_label(self):
        self.client.force_login(self.owner)
        save=reverse("save_ball_label",args=[self.game.pk])
        response=self.client.get(reverse("ball_labels",args=[self.game.pk]))
        self.assertContains(response,"hard_negative_reason")
        self.assertEqual(self.client.post(save,{
            "time_seconds":"1.3","ball_absent":"yes",
            "hard_negative_reason":"jersey"}).status_code,302)
        item=self.game.ball_frames.get(time_seconds=1.3)
        self.assertTrue(item.ball_absent)
        self.assertEqual(item.hard_negative_reason,"jersey")
        self.client.post(save,{
            "time_seconds":"1.3","x":".2","y":".25",
            "w":".04","h":".05","hard_negative_reason":"jersey"})
        item.refresh_from_db()
        self.assertFalse(item.ball_absent)
        self.assertEqual(item.hard_negative_reason,"")
        self.client.post(save,{
            "time_seconds":"1.8","ball_absent":"yes",
            "hard_negative_reason":"unsafe-invented-value"})
        self.assertFalse(self.game.ball_frames.filter(time_seconds=1.8).exists())

    def test_training_approval_resets_when_label_changes(self):
        self.client.force_login(self.owner)
        self._save(self.game)
        self.client.post(reverse("approve_training_data",args=[self.game.pk]),{
            "approve":"yes", "rights_note":"Authorized in writing by gym owner, 2026."
        })
        self.game.refresh_from_db()
        self.assertTrue(self.game.ball_training_approved)
        self._save(self.game,time="1.8",absent=True)
        self.game.refresh_from_db()
        self.assertFalse(self.game.ball_training_approved)
        label=self.game.ball_frames.first()
        self.client.post(reverse("approve_training_data",args=[self.game.pk]),{
            "approve":"yes","rights_note":"Authorized in writing by gym owner, 2026."
        })
        self.client.post(reverse("delete_ball_label",args=[self.game.pk,label.pk]))
        self.game.refresh_from_db()
        self.assertFalse(self.game.ball_training_approved)

    def test_frame_jpeg_valid_media_and_missing(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("ball_frame_jpg",args=[self.game.pk])+"?t=nan").status_code,400)
        with patch("highlights.ball_views.extract_frame_jpeg",return_value=b"\xff\xd8\xff\xd9"):
            response=self.client.get(reverse("ball_frame_jpg",args=[self.game.pk])+"?t=1")
            self.assertEqual(response.status_code,200)
            self.assertEqual(response["Cache-Control"],"private, no-store")
            self.assertEqual(response.content[:2],b"\xff\xd8")

    def test_consent_export_split_by_entire_games(self):
        self.client.force_login(self.owner)
        self._save(self.game,time="1.2")
        self._save(self.game2,time="1.2")
        self._save(self.game2,time="2.0",absent=True)
        for game in (self.game,self.game2):
            self.client.post(reverse("approve_training_data",args=[game.pk]),{
                "approve":"yes","rights_note":"CC0 footage approved for private research and training"
            })
        # In real use FFmpeg extracts exact JPEG bytes; mock only that IO boundary.
        frame=np.zeros((48,64,3),dtype=np.uint8)
        ok,encoded=cv2.imencode(".jpg",frame)
        self.assertTrue(ok)
        with tempfile.TemporaryDirectory() as root:
            with patch("highlights.management.commands.export_ball_dataset.extract_frame_jpeg",
                       return_value=encoded.tobytes()):
                # Ensure train has a positive sample independent of randomized game split.
                self._save(self.game2,time="1.2")  # This resets approval.
                self.client.post(reverse("approve_training_data",args=[self.game2.pk]),{
                    "approve":"yes","rights_note":"CC0 footage approved for private research and training"
                })
                data=build_dataset(self.owner.pk,root,game_ids=[self.game.pk,self.game2.pk])
            self.assertEqual(len(data["games"]),2)
            train_ids={r["game_id"] for r in data["samples"] if r["split"]=="train"}
            val_ids={r["game_id"] for r in data["samples"] if r["split"]=="val"}
            self.assertFalse(train_ids & val_ids)
            self.assertTrue((Path(root)/"manifest.json").exists())
            for row in data["samples"]:
                self.assertTrue((Path(root)/row["image"]).exists())
                self.assertTrue((Path(root)/row["label"]).exists())

    def test_reject_owner_cross_export_and_unapproved(self):
        self.client.force_login(self.owner)
        self._save(self.game)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError,"TWO"):
                build_dataset(self.owner.pk,root)
        self.client.force_login(self.other)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                build_dataset(self.other.pk,root,game_ids=[self.game.pk])
