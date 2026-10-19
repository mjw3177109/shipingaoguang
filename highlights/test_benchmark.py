"""Deterministic regression tests. All numbers are SYNTHETIC, not model accuracy."""
import json
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Game, ShotAnnotation, Venue
from .vision.evaluate import evaluate, format_report
from .vision.tune import search


class OfflineMetricsTests(TestCase):
    def setUp(self):
        self.gt = {
            "schema_version": 1, "video_duration_seconds": 120,
            "complete_within_scope": True,
            "events": [{"type": "shot_attempt", "time_seconds": i} for i in (12, 36, 87)],
        }
        self.candidates = {
            "schema_version": 2, "video_duration_seconds": 120,
            "analysis": {
                "incomplete_scan": False, "analyzed_until_seconds": 120,
                "elapsed_seconds": 4.5,
            },
            "candidates": [
                {"start_seconds": 8, "end_seconds": 20},
                {"start_seconds": 33, "end_seconds": 42},
                {"start_seconds": 48, "end_seconds": 52},
            ],
        }

    def test_precision_recall_false_positive_and_cost(self):
        result = evaluate(self.candidates, self.gt, gpu_hourly_usd=.6)
        p = result["performance"]
        self.assertEqual((p["true_positive_matches"], p["missed_shots"],
                          p["false_positive_clips"]), (2, 1, 1))
        self.assertAlmostEqual(p["recall"], .6667, places=4)
        self.assertAlmostEqual(p["precision"], .6667, places=4)
        self.assertEqual(p["false_positives_per_10min"], 5.0)
        self.assertGreater(result["compute"]["estimated_gpu_usd"], 0)
        self.assertIn("NOT made", format_report(result).replace("不能当作自动进球判定", "NOT made"))

    def test_one_long_clip_cannot_claim_two_unique_matches(self):
        truth = dict(self.gt, events=[
            {"type": "shot_attempt", "time_seconds": 12},
            {"type": "shot_attempt", "time_seconds": 16},
        ])
        clips = dict(self.candidates, candidates=[{"start_seconds": 10, "end_seconds": 20}])
        result = evaluate(clips, truth)["performance"]
        self.assertEqual(result["true_positive_matches"], 1)
        self.assertEqual(result["missed_shots"], 1)
        self.assertEqual(result["raw_event_coverage"], 1.0)

    def test_partial_scans_do_not_count_unseen_shots_as_misses(self):
        clips = dict(self.candidates, analysis={
            "incomplete_scan": True, "analyzed_until_seconds": 55,
            "elapsed_seconds": 4.5,
        })
        result = evaluate(clips, self.gt)
        self.assertFalse(result["scope"]["full_video"])
        self.assertEqual(result["scope"]["excluded_unscanned_events"], 1)
        self.assertEqual(result["performance"]["ground_truth_shots"], 2)
        self.assertEqual(result["performance"]["missed_shots"], 0)

    def test_reject_incomplete_or_mismatched_ground_truth(self):
        with self.assertRaisesRegex(ValueError, "Complete annotation"):
            evaluate(self.candidates, dict(self.gt, complete_within_scope=False))
        with self.assertRaisesRegex(ValueError, "duration mismatch"):
            evaluate(self.candidates, dict(self.gt, video_duration_seconds=150))
        with self.assertRaisesRegex(ValueError, "game_id mismatch"):
            evaluate(dict(self.candidates, game_id=1), dict(self.gt, game_id=2))
        with self.assertRaisesRegex(ValueError, "finite"):
            bad = dict(self.gt, events=[{"type": "shot_attempt", "time_seconds": float("nan")}])
            evaluate(self.candidates, bad)

    def test_empty_game_not_misrepresented_as_perfect_recall(self):
        gt = dict(self.gt, events=[])
        clips = dict(self.candidates, candidates=[])
        result = evaluate(clips, gt)
        self.assertIsNone(result["performance"]["recall"])
        self.assertIsNone(result["performance"]["f1"])

    def test_tolerance_bounded(self):
        self.assertEqual(evaluate(self.candidates, self.gt, tolerance=0)["performance"]["true_positive_matches"], 2)
        with self.assertRaises(ValueError):
            evaluate(self.candidates, self.gt, tolerance=4)

    def test_replay_sweep_uses_cached_ball_coordinates(self):
        truth = dict(self.gt, events=[{"type": "shot_attempt", "time_seconds": 14}])
        raw = {
            "schema_version": 1, "video_duration_seconds": 120,
            "hoops": [{"x": .4, "y": .2, "w": .1, "h": .06}],
            "game_id": 6,
            "analysis": {"incomplete_scan": False, "analyzed_until_seconds": 120,
                         "elapsed_seconds": 5},
            "observations": [
                {"time": 13, "x": .45, "y": .23, "confidence": .8},
                {"time": 14, "x": .46, "y": .23, "confidence": .75},
                {"time": 40, "x": .45, "y": .23, "confidence": .25},
                {"time": 41, "x": .46, "y": .23, "confidence": .24},
            ],
        }
        rows = search(raw, truth, min_hits=(1, 2), confidence=(.2, .5))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["min_confidence"], .5)
        self.assertEqual(rows[0]["candidate_count"], 1)

    def test_sweep_invalid_numeric_observation_rejected(self):
        raw = {
            "schema_version": 1, "video_duration_seconds": 100,
            "hoops": [{"x": .4, "y": .2, "w": .1, "h": .06}],
            "analysis": {"incomplete_scan": False},
            "observations": [{"time": float("nan"), "x": .4, "y": .2, "confidence": .8}],
        }
        with self.assertRaises(ValueError):
            search(raw, dict(self.gt, video_duration_seconds=100))


class IndependentLabelingTests(TestCase):
    def setUp(self):
        user = get_user_model()
        self.owner = user.objects.create_user("owner2", password="s3cret111")
        self.other = user.objects.create_user("other2", password="s3cret111")
        self.venue = Venue.objects.create(owner=self.owner, name="Court X")
        self.game = Game.objects.create(
            venue=self.venue, title="Fixture", duration_seconds=120,
            video=SimpleUploadedFile("dummy.mp4", b"file"),
        )

    def test_private_labeling_export_and_invalidation(self):
        url = reverse("add_shot_annotation", args=[self.game.pk])
        export = reverse("download_ground_truth", args=[self.game.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {"time_seconds": "12"}).status_code, 404)
        self.assertEqual(self.client.get(export).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(export).status_code, 409)
        self.assertEqual(self.client.post(url, {"time_seconds": "12.5"}).status_code, 302)
        self.assertEqual(self.client.post(url, {"time_seconds": "12.5"}).status_code, 302)
        self.assertEqual(ShotAnnotation.objects.filter(game=self.game).count(), 1)
        self.client.post(reverse("confirm_ground_truth", args=[self.game.pk]), {"complete": "yes"})
        data = self.client.get(export)
        self.assertEqual(data.status_code, 200)
        self.assertTrue(data.json()["complete_within_scope"])
        self.assertEqual(data.json()["events"][0]["time_seconds"], 12.5)
        self.client.post(url, {"time_seconds": "40"})
        self.game.refresh_from_db()
        self.assertFalse(self.game.annotations_complete)
        self.assertEqual(self.client.get(export).status_code, 409)

    def test_deletion_revokes_confirmation(self):
        self.client.force_login(self.owner)
        ann = ShotAnnotation.objects.create(game=self.game, time_seconds=16)
        self.client.post(reverse("confirm_ground_truth", args=[self.game.pk]), {"complete": "yes"})
        self.assertTrue(Game.objects.get(pk=self.game.pk).annotations_complete)
        self.assertEqual(self.client.post(reverse("delete_shot_annotation",
                args=[self.game.pk, ann.pk])).status_code, 302)
        self.assertFalse(Game.objects.get(pk=self.game.pk).annotations_complete)

    def test_out_of_range_label_rejected_and_ui_shown(self):
        self.client.force_login(self.owner)
        url = reverse("add_shot_annotation", args=[self.game.pk])
        for val in ("nan", "-1", "120.01"):
            self.assertEqual(self.client.post(url, {"time_seconds": val}).status_code, 302)
        self.assertFalse(self.game.shot_annotations.exists())
        response = self.client.get(reverse("game", args=[self.game.pk]))
        self.assertContains(response, "truth-seconds")
        self.assertContains(response, "confirm_ground_truth")
