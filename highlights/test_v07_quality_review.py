"""V0.7 quality review lifecycle, paged labeling, strict exporter and legacy pilot."""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from .models import Game, Venue, BallFrameAnnotation, BallFrameSuggestion
from .management.commands.export_ball_dataset import build_dataset
from .vision.review_readiness_v07 import review_readiness


class ReviewReadinessTests(SimpleTestCase):
    def test_four_real_pilot_labels_remain_blocked(self):
        pilot={
            "samples":[{"ball_absent":False} for _ in range(4)],
            "games":[{"id":1},{"id":2}],
            "synthetic":False,
        }
        result=review_readiness(pilot)
        self.assertEqual(result["positive_frames"],4)
        self.assertEqual(result["explicit_negative_frames"],0)
        self.assertEqual(result["verified_frames"],0)
        self.assertFalse(result["eligible_for_independent_model_benchmark"])
        self.assertFalse(result["production_release"])

    def test_true_verified_flag_requires_review_user(self):
        rows=[{"ball_absent":False,"quality_verified":True,
               "quality_verified_by_id":None}]
        out=review_readiness({"samples":rows,"games":[]})
        self.assertEqual(out["verified_frames"],0)


class BallReviewWebTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.mkdtemp()
        settings=override_settings(MEDIA_ROOT=self.tmp)
        settings.enable()
        self.addCleanup(settings.disable)
        self.addCleanup(shutil.rmtree,self.tmp,ignore_errors=True)
        users=get_user_model()
        self.owner=users.objects.create_user(username="v07owner",password="safePass01")
        self.other=users.objects.create_user(username="v07other",password="safePass01")
        self.venue=Venue.objects.create(owner=self.owner,name="Court")
        self.game=Game.objects.create(venue=self.venue,title="V07 Game",
            duration_seconds=30,video=SimpleUploadedFile("video.mp4",b"dummy-bytes"))
        self.label=BallFrameAnnotation.objects.create(
            game=self.game,time_seconds=2,x=.4,y=.3,w=.03,h=.04)

    def _verify(self, label=None):
        label=label or self.label
        return self.client.post(reverse("verify_ball_label",
               args=[self.game.pk,label.pk]),{"verified":"yes"})

    def test_review_owner_only_invalidation_and_re_review(self):
        url=reverse("verify_ball_label",args=[self.game.pk,self.label.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url,{"verified":"yes"}).status_code,404)
        self.client.force_login(self.owner)
        page=self.client.get(reverse("ball_labels",args=[self.game.pk]))
        self.assertContains(page,"看图 / 填入旧框")
        self.assertContains(page,"待复核 1")
        self.assertEqual(self.client.post(url,{}).status_code,302)
        self.label.refresh_from_db()
        self.assertFalse(self.label.quality_verified)
        self.assertEqual(self._verify().status_code,302)
        self.label.refresh_from_db()
        self.assertTrue(self.label.quality_verified)
        self.assertEqual(self.label.quality_verified_by,self.owner)
        self.assertIsNotNone(self.label.quality_verified_at)
        self.client.post(reverse("approve_training_data",args=[self.game.pk]),{
            "approve":"yes","rights_note":"Authorized by recording owner for model training"
        })
        self.game.refresh_from_db()
        self.assertTrue(self.game.ball_training_approved)
        self.client.post(reverse("save_ball_label",args=[self.game.pk]),{
            "time_seconds":"2","x":".42","y":".31","w":".035","h":".045",
        })
        self.game.refresh_from_db();self.label.refresh_from_db()
        self.assertFalse(self.game.ball_training_approved)
        self.assertFalse(self.label.quality_verified)
        self.assertIsNone(self.label.quality_verified_at)
        self.assertIsNone(self.label.quality_verified_by)

    def test_revocation_requires_owner_and_withdraws_train_approval(self):
        self.client.force_login(self.owner)
        self._verify()
        self.client.post(reverse("approve_training_data",args=[self.game.pk]),{
            "approve":"yes","rights_note":"Consent granted for sports model training"
        })
        rev=reverse("revoke_ball_verification",args=[self.game.pk,self.label.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(rev).status_code,404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(rev).status_code,302)
        self.label.refresh_from_db();self.game.refresh_from_db()
        self.assertFalse(self.label.quality_verified)
        self.assertFalse(self.game.ball_training_approved)

    def test_save_and_next_follows_unlabeled_suggestion(self):
        self.client.force_login(self.owner)
        BallFrameSuggestion.objects.create(game=self.game,time_seconds=8,
                        reason="coverage",score=.2)
        BallFrameSuggestion.objects.create(game=self.game,time_seconds=14,
                        reason="motion",score=.7)
        response=self.client.post(reverse("save_ball_label",args=[self.game.pk]),{
            "time_seconds":"8","ball_absent":"yes","hard_negative_reason":"jersey",
            "save_next":"yes",
        })
        self.assertEqual(response.status_code,302)
        self.assertIn("?t=14.00",response["Location"])
        self.assertFalse(self.game.ball_suggestions.filter(time_seconds=8).exists())
        self.assertTrue(self.game.ball_suggestions.filter(time_seconds=14).exists())

    def test_pagination_many_labels_and_quality_counts(self):
        self.client.force_login(self.owner)
        BallFrameAnnotation.objects.bulk_create([
            BallFrameAnnotation(game=self.game,time_seconds=3+i*.1,ball_absent=True)
            for i in range(64)])
        first=self.client.get(reverse("ball_labels",args=[self.game.pk]))
        self.assertContains(first,"第 1 / 2 页")
        self.assertContains(first,"已标 65 帧")
        second=self.client.get(reverse("ball_labels",args=[self.game.pk])+"?page=2")
        self.assertContains(second,"第 2 / 2 页")

    def test_strict_export_rejects_unverified_and_requires_three_way(self):
        # Boundary must be enforced before video frame IO.
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"requires --three-way"):
                build_dataset(self.owner.pk,folder,verified_only=True)
        self.client.force_login(self.owner)
        self.client.post(reverse("approve_training_data",args=[self.game.pk]),{
            "approve":"yes","rights_note":"Authorized footage for machine learning review"
        })
        # Only one game in fixture => strict three-way fails, even if flagged approved.
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"at least six"):
                build_dataset(self.owner.pk,folder,three_way=True,verified_only=True)

    def test_strict_export_six_games_verified_then_unverified_rejected(self):
        games=[self.game]
        for i in range(5):
            games.append(Game.objects.create(
                venue=self.venue,title=f"Game {i}",duration_seconds=30,
                video=SimpleUploadedFile(f"g{i}.mp4",b"fake video bytes")))
        for game in games:
            for t,absent in ((4,False),(10,True)):
                if not game.ball_frames.filter(time_seconds=t).exists():
                    BallFrameAnnotation.objects.create(
                        game=game,time_seconds=t,ball_absent=absent,
                        x=0 if absent else .3,y=0 if absent else .2,
                        w=0 if absent else .03,h=0 if absent else .03,
                        hard_negative_reason="jersey" if absent else "")
            Game.objects.filter(pk=game.pk).update(
                ball_training_approved=True,
                ball_training_rights="Written consent granted for basketball training")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,"review incomplete"):
                build_dataset(self.owner.pk,folder,three_way=True,verified_only=True)
        for game in games:
            for frame in game.ball_frames.all():
                frame.quality_verified=True
                frame.quality_verified_by=self.owner
                from django.utils import timezone
                frame.quality_verified_at=timezone.now()
                frame.save(update_fields=["quality_verified","quality_verified_by","quality_verified_at"])
        with tempfile.TemporaryDirectory() as folder:
            with patch("highlights.management.commands.export_ball_dataset.extract_frame_jpeg",
                       return_value=b"JPEG_TEST_CONTENT"):
                manifest=build_dataset(self.owner.pk,folder,three_way=True,verified_only=True)
            self.assertTrue(manifest["label_review_required"])
            self.assertEqual(len(manifest["samples"]),13)
            self.assertTrue(all(row["quality_verified"] for row in manifest["samples"]))
            report=json.loads((Path(folder)/"quality_report.json").read_text())
            self.assertTrue(report["all_exported_frames_verified"])
            self.assertEqual(report["unverified_frames"],0)
            self.assertTrue((Path(folder)/"images/test").exists())
