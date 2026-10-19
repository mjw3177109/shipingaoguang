"""V0.5 stdlib-only three-way split and sealed-test comparison tests."""
import hashlib
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from scripts.synthetic_v05_compare_smoke import create_fixture
from .vision.split_v05 import three_way_games, audit_three_way
from .vision.compare_v05 import compare_dataset


class ThreeWaySplitTests(SimpleTestCase):
    def test_min_six_games_and_no_overlap(self):
        with self.assertRaises(ValueError):
            three_way_games(range(5))
        with self.assertRaises(ValueError):
            three_way_games([1,1,2,3,4,5,6])
        a=three_way_games(range(6))
        b=three_way_games(range(6))
        self.assertEqual(a,b)
        self.assertEqual(set(a.values()),{"train","val","test"})
        self.assertEqual(list(a.values()).count("train"),2)
        self.assertEqual(list(a.values()).count("val"),2)
        self.assertEqual(list(a.values()).count("test"),2)

    def test_three_way_quality_needs_real_positives_and_negatives(self):
        with tempfile.TemporaryDirectory() as temp:
            root=create_fixture(temp,positives=20,negatives=10)
            games=json.loads((root/"manifest.json").read_text())["games"]
            check=audit_three_way(games)
            self.assertTrue(check["data_coverage_ready"])
            self.assertEqual(check["by_split"]["test"]["games"],2)
            for g in games:
                g["negative"]=0
            self.assertFalse(audit_three_way(games)["data_coverage_ready"])
            # Real low-data pilot: no negatives, only two games, cannot pass.
            low=[{"id":1,"split":"train","positive":2,"negative":0},
                 {"id":2,"split":"test","positive":2,"negative":0}]
            self.assertFalse(audit_three_way(low)["data_coverage_ready"])


class CompareModelsTests(SimpleTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=create_fixture(self.temp.name)
        self.baseline=self.root/"baseline.json"
        self.challenger=self.root/"challenger.json"

    def evaluate(self):
        return compare_dataset(self.root,self.baseline,self.challenger)

    def test_synthetic_best_possible_score_still_blocked(self):
        report=self.evaluate()
        self.assertEqual(report["baseline"]["f1"],0.)
        self.assertEqual(report["challenger"]["recall"],1.)
        self.assertEqual(report["challenger"]["precision"],1.)
        self.assertTrue(report["data_audit"]["data_coverage_ready"])
        self.assertFalse(report["checks"]["non_synthetic_real_data"])
        self.assertFalse(report["eligible_for_independent_human_review"])
        self.assertFalse(report["production_release"])
        self.assertEqual(report["evaluation_scope"],"SEALED_TEST_ONLY")

    def test_missing_predictions_are_not_silently_treated_as_empty(self):
        x=json.loads(self.baseline.read_text())
        x["predictions"].pop(next(iter(x["predictions"])))
        self.baseline.write_text(json.dumps(x))
        with self.assertRaisesRegex(ValueError,"coverage mismatch"):
            self.evaluate()

    def test_untrusted_prediction_bbox_rejected(self):
        x=json.loads(self.challenger.read_text())
        key=next(iter(x["predictions"]))
        x["predictions"][key]=[{"bbox":[float("nan"),.2,.7,.8],"score":.6}]
        self.challenger.write_text(json.dumps(x))
        with self.assertRaisesRegex(ValueError,"nonfinite"):
            self.evaluate()

    def test_cross_split_duplicate_frames_block_candidate_gate(self):
        manifest=json.loads((self.root/"manifest.json").read_text())
        first=next(x for x in manifest["samples"] if x["split"]=="train")
        held=next(x for x in manifest["samples"] if x["split"]=="test")
        copied=(self.root/first["image"]).read_bytes()
        (self.root/held["image"]).write_bytes(copied)
        held["sha256_image"]=hashlib.sha256(copied).hexdigest()
        (self.root/"manifest.json").write_text(json.dumps(manifest))
        report=self.evaluate()
        self.assertFalse(report["checks"]["no_cross_split_identical_image_hashes"])
        self.assertFalse(report["eligible_for_independent_human_review"])

    def test_tampered_image_hash_is_hard_error(self):
        first=next((self.root/"images/test").glob("*.jpg"))
        first.write_bytes(b"MODIFIED AFTER MANIFEST")
        with self.assertRaisesRegex(ValueError,"SHA256 mismatch"):
            self.evaluate()

    def test_sealed_test_cannot_be_empty_or_claim_different_game_split(self):
        manifest=json.loads((self.root/"manifest.json").read_text())
        row=next(x for x in manifest["samples"] if x["split"]=="test")
        row["split"]="train"
        (self.root/"manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,"split boundary"):
            self.evaluate()
