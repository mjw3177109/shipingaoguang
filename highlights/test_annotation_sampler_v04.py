"""V0.4 negative-mining queue regression. Synthetic frames are mechanical fixtures."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import BallFrameAnnotation, BallFrameSuggestion, Game, Venue
from .vision.annotation_sampler import REASONS, choose_suggestions, scan_annotation_frames
from .vision.data_quality import audit_games


class CpuSamplerUnitTests(TestCase):
    def test_diverse_priority_selection_and_no_fake_labels(self):
        rows=[]
        for i in range(15):
            rows.append({"time_seconds":i*1.2,"reason":REASONS[i%3],"score":.5+(i%2)*.1})
        chosen=choose_suggestions(rows,20,limit=9)
        self.assertLessEqual(len(chosen),9)
        self.assertEqual(chosen,sorted(chosen,key=lambda x:x["time_seconds"]))
        self.assertTrue({"coverage","orange_ambiguous"}.issubset({c["reason"] for c in chosen}))
        self.assertTrue(all(set(x)=={"time_seconds","reason","score"} for x in chosen))
        with self.assertRaises(ValueError):
            choose_suggestions([{"time_seconds":float("nan"),"score":.5,"reason":"coverage"}],20)

    def test_cpu_sampler_decodes_actual_moving_orange_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            source=str(Path(folder)/"sample.avi")
            writer=cv2.VideoWriter(source,cv2.VideoWriter_fourcc(*"MJPG"),8,(320,240))
            if not writer.isOpened():
                self.skipTest("MJPG encoder unavailable on runner")
            for idx in range(48):
                image=np.zeros((240,320,3),np.uint8)
                image[:]=(40,55,60)
                if idx>=8:
                    cv2.circle(image,(15+(idx*5)%275,100),11,(0,125,240),-1)
                writer.write(image)
            writer.release()
            result=scan_annotation_frames(source,limit=10,max_samples=24)
            self.assertEqual(result["analysis"]["device"],"cpu")
            self.assertGreaterEqual(result["analysis"]["decoded_frames"],8)
            self.assertGreater(len(result["suggestions"]),0)
            self.assertTrue(all(r["reason"] in REASONS for r in result["suggestions"]))
            self.assertFalse(any("ball_absent" in r for r in result["suggestions"]))

    def test_quality_check_passes_balanced_four_game_coverage_only(self):
        rows=[
            {"id":1,"split":"train","positive":40,"negative":12},
            {"id":2,"split":"train","positive":40,"negative":12},
            {"id":3,"split":"val","positive":40,"negative":12},
            {"id":4,"split":"val","positive":40,"negative":12},
        ]
        report=audit_games(rows)
        self.assertTrue(report["basic_data_coverage_ready"])
        self.assertTrue(all(report["checks"].values()))

    def test_data_quality_flags_tiny_positive_only_data_as_unready(self):
        sample=[{"id":1,"split":"train","positive":2,"negative":0},
                {"id":2,"split":"val","positive":2,"negative":0}]
        audit=audit_games(sample)
        self.assertFalse(audit["basic_data_coverage_ready"])
        self.assertFalse(audit["checks"]["train_has_both_classes"])
        self.assertEqual(audit["explicit_negative_frames"],0)
        with self.assertRaises(ValueError):
            audit_games(sample+sample)


class CpuSamplerDjangoTests(TestCase):
    def setUp(self):
        u=get_user_model()
        self.owner=u.objects.create_user("v04-owner",password="safePass1!")
        self.other=u.objects.create_user("v04-other",password="safePass1!")
        self.venue=Venue.objects.create(owner=self.owner,name="Gym 1")
        self.game=Game.objects.create(
            venue=self.venue,title="Court",duration_seconds=60,
            video=SimpleUploadedFile("video.mp4",b"unused-test-bytes"))
        self.queue=reverse("queue_ball_sampling",args=[self.game.pk])

    def test_owner_only_queue_worker_and_consumption(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.queue).status_code,404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.queue).status_code,302)
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_state,"queued")
        with patch("highlights.management.commands.runworker.scan_annotation_frames",return_value={
            "suggestions":[{"time_seconds":12.5,"reason":"orange_ambiguous","score":.91},
                           {"time_seconds":18.5,"reason":"coverage","score":.25}]}):
            from .management.commands.runworker import Command
            self.assertTrue(Command()._process_one())
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_state,"ready")
        self.assertEqual(self.game.ball_suggestions.count(),2)
        self.assertEqual(self.game.ball_frames.count(),0)
        page=self.client.get(reverse("ball_labels",args=[self.game.pk]))
        self.assertContains(page,"12.50")
        self.client.post(reverse("save_ball_label",args=[self.game.pk]),{
            "time_seconds":"12.5","ball_absent":"yes"})
        self.assertEqual(self.game.ball_frames.count(),1)
        self.assertTrue(self.game.ball_frames.first().ball_absent)
        self.assertFalse(self.game.ball_suggestions.filter(time_seconds=12.5).exists())
        self.assertTrue(self.game.ball_suggestions.filter(time_seconds=18.5).exists())

    def test_queue_repeat_and_failure_does_not_erase_labels(self):
        self.client.force_login(self.owner)
        BallFrameAnnotation.objects.create(
            game=self.game,time_seconds=6,ball_absent=True)
        self.client.post(self.queue)
        self.client.post(self.queue)
        self.assertEqual(self.game.ball_suggestions.count(),0)
        with patch("highlights.management.commands.runworker.scan_annotation_frames",
                   side_effect=ValueError("cannot decode")):
            from .management.commands.runworker import Command
            self.assertTrue(Command()._process_one())
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_state,"failed")
        self.assertIn("cannot decode",self.game.ball_sampling_error)
        self.assertEqual(self.game.ball_frames.count(),1)
        self.client.post(self.queue)
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_state,"queued")

    def test_scan_skips_times_with_existing_verified_labels(self):
        self.client.force_login(self.owner)
        BallFrameAnnotation.objects.create(game=self.game,time_seconds=12.5,ball_absent=True)
        self.client.post(self.queue)
        with patch("highlights.management.commands.runworker.scan_annotation_frames",return_value={
            "suggestions":[{"time_seconds":12.5,"reason":"coverage","score":.2},
                           {"time_seconds":20,"reason":"motion","score":.9}]}):
            from .management.commands.runworker import Command
            Command()._process_one()
        self.assertEqual(list(self.game.ball_suggestions.values_list("time_seconds",flat=True)),[20.])
        self.assertEqual(self.game.ball_frames.count(),1)
