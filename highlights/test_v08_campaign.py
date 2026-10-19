"""V0.8 campaign, repeated CPU sampling and cross-tenant backlog tests."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import BallFrameAnnotation, BallFrameSuggestion, Game, Venue
from .vision.campaign_v08 import build_campaign_report


class CampaignMetricsTests(SimpleTestCase):
    def test_counts_are_human_labels_only_and_gate_blocked(self):
        rows=[
            {"id":1,"title":"A","venue_name":"Court",
             "positive":2,"negative":1,"verified":2,"pending":100,
             "approved":False,"sampling_state":"ready","sampling_rounds":3},
            {"id":2,"title":"B","venue_name":"Court",
             "positive":0,"negative":0,"verified":0,"pending":20,
             "approved":True,"sampling_state":"ready","sampling_rounds":1},
        ]
        data=build_campaign_report(rows)
        self.assertEqual(data["positive_frames"],2)
        self.assertEqual(data["explicit_negative_frames"],1)
        self.assertEqual(data["pending_cpu_review_times"],120)
        self.assertEqual(data["eligible_games"],0)
        self.assertFalse(data["basic_collection_targets_met"])
        self.assertFalse(data["production_release"])
        self.assertEqual(data["progress"]["positive"]["remaining"],118)

    def test_empty_account_and_malformed_counts(self):
        self.assertEqual(build_campaign_report([])["total_games"],0)
        with self.assertRaisesRegex(ValueError,"Verified frames"):
            build_campaign_report([{"id":1,"title":"A","venue_name":"Court",
                "positive":0,"negative":0,"verified":1,"pending":0,
                "approved":True,"sampling_state":"idle","sampling_rounds":0}])


class CampaignWebAndWorkerTests(TestCase):
    def setUp(self):
        temp=tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree,temp,ignore_errors=True)
        override=override_settings(MEDIA_ROOT=temp)
        override.enable();self.addCleanup(override.disable)
        U=get_user_model()
        self.owner=U.objects.create_user(username="v08_owner",password="test-password-12")
        self.stranger=U.objects.create_user(username="v08_stranger",password="test-password-12")
        self.venue=Venue.objects.create(owner=self.owner,name="PrivateGym")
        self.foreign_venue=Venue.objects.create(owner=self.stranger,name="HiddenGym")
        self.game=Game.objects.create(
            venue=self.venue,title="My Game",duration_seconds=60,
            video=SimpleUploadedFile("match1.mp4",b"private-game-bytes"))
        self.foreign=Game.objects.create(
            venue=self.foreign_venue,title="Secret Opponent",duration_seconds=60,
            video=SimpleUploadedFile("secret.mp4",b"other-user-bytes"))

    def test_private_multi_game_dashboard_and_json(self):
        BallFrameAnnotation.objects.create(
            game=self.game,time_seconds=1,x=.3,y=.2,w=.04,h=.03)
        BallFrameAnnotation.objects.create(
            game=self.game,time_seconds=2,ball_absent=True,
            hard_negative_reason="jersey")
        BallFrameSuggestion.objects.create(
            game=self.game,time_seconds=3,reason="motion",score=.6)
        BallFrameAnnotation.objects.create(
            game=self.foreign,time_seconds=3,x=.3,y=.2,w=.03,h=.03)
        self.client.force_login(self.owner)
        page=self.client.get(reverse("ball_campaign"))
        self.assertEqual(page.status_code,200)
        self.assertContains(page,"My Game")
        self.assertNotContains(page,"Secret Opponent")
        report=self.client.get(reverse("ball_campaign_json"))
        self.assertEqual(report.status_code,200)
        self.assertEqual(report["Cache-Control"],"private, no-store")
        stats=report.json()
        self.assertEqual(stats["positive_frames"],1)
        self.assertEqual(stats["explicit_negative_frames"],1)
        self.assertEqual(stats["pending_cpu_review_times"],1)
        self.assertEqual(stats["total_games"],1)
        self.assertFalse(stats["production_release"])
        self.client.force_login(self.stranger)
        self.assertNotContains(self.client.get(reverse("ball_campaign")),"My Game")
        self.assertEqual(self.client.get(reverse("ball_campaign_json")).json()["total_games"],1)

    def test_multiple_sampling_rounds_keep_pending_review_and_exclude_human_labels(self):
        self.client.force_login(self.owner)
        url=reverse("queue_ball_sampling",args=[self.game.pk])
        BallFrameAnnotation.objects.create(game=self.game,time_seconds=4,ball_absent=True)
        first={"suggestions":[
            {"time_seconds":1.0,"reason":"coverage","score":.2},
            {"time_seconds":4.0,"reason":"motion","score":.7}]}
        second={"suggestions":[
            {"time_seconds":1.10,"reason":"motion","score":.8},
            {"time_seconds":2.0,"reason":"orange_ambiguous","score":.9}]}
        self.assertEqual(self.client.post(url).status_code,302)
        self.client.post(url)  # already queued; round cannot be incremented twice
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_round,1)
        with patch("highlights.management.commands.runworker.scan_annotation_frames",
                   return_value=first) as scan:
            from .management.commands.runworker import Command
            self.assertTrue(Command()._process_one())
            self.assertEqual(scan.call_args.kwargs["sampling_round"],0)
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_state,"ready")
        self.assertEqual(list(self.game.ball_suggestions.values_list("time_seconds",flat=True)),[1.0])
        self.assertEqual(self.client.post(url).status_code,302)
        with patch("highlights.management.commands.runworker.scan_annotation_frames",
                   return_value=second) as scan:
            from .management.commands.runworker import Command
            self.assertTrue(Command()._process_one())
            self.assertEqual(scan.call_args.kwargs["sampling_round"],1)
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_round,2)
        self.assertEqual(
            list(self.game.ball_suggestions.values_list("time_seconds",flat=True)),
            [1.0,2.0])
        self.assertEqual(self.game.ball_frames.count(),1)
        self.assertTrue(self.game.ball_frames.get(time_seconds=4).ball_absent)

    def test_round_queue_denies_foreign_owner(self):
        self.client.force_login(self.stranger)
        response=self.client.post(reverse("queue_ball_sampling",args=[self.game.pk]))
        self.assertEqual(response.status_code,404)
        self.game.refresh_from_db()
        self.assertEqual(self.game.ball_sampling_round,0)

    def test_thumbnail_gallery_uses_private_frame_endpoint(self):
        self.client.force_login(self.owner)
        BallFrameSuggestion.objects.create(
            game=self.game,time_seconds=2.4,reason="motion",score=.8)
        page=self.client.get(reverse("ball_labels",args=[self.game.pk]))
        self.assertContains(page,"待检查真实视频帧预览")
        self.assertContains(page,reverse("ball_frame_jpg",args=[self.game.pk]))
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("ball_frame_jpg",args=[self.game.pk])+"?t=2.4").status_code,404)

    def test_worker_pending_cap(self):
        self.client.force_login(self.owner)
        BallFrameSuggestion.objects.bulk_create([
            BallFrameSuggestion(game=self.game,time_seconds=5+i*.1,
                reason="motion",score=.5) for i in range(300)])
        self.client.post(reverse("queue_ball_sampling",args=[self.game.pk]))
        with patch("highlights.management.commands.runworker.scan_annotation_frames",
                   return_value={"suggestions":[{"time_seconds":45.0,"reason":"coverage","score":.2}]}):
            from .management.commands.runworker import Command
            Command()._process_one()
        self.assertEqual(self.game.ball_suggestions.count(),300)
