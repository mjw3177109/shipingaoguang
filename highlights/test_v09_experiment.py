"""V0.9 deterministic tests for the real-data gate and research-only runner."""
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from scripts.v09_synthetic_jpeg_fixture import create_synthetic_jpeg_dataset
from .vision.preflight_v09 import inspect_dataset
from .vision.experiment_v09 import run_experiment


class CpuExperimentGateTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = create_synthetic_jpeg_dataset(Path(self.temp.name) / "dataset")
        self.manifest_path = self.root / "manifest.json"

    def _manifest(self):
        return json.loads(self.manifest_path.read_text())

    def _replace_manifest(self, data):
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    def test_synthetic_even_perfectly_formed_and_verified_is_blocked(self):
        report = inspect_dataset(self.root)
        self.assertEqual(report["positive_frames"], 160)
        self.assertEqual(report["explicit_negative_frames"], 80)
        self.assertEqual(report["games"], 8)
        self.assertFalse(report["checks"]["explicitly_not_synthetic"])
        self.assertTrue(report["checks"]["independent_split_basic_coverage"])
        self.assertTrue(report["checks"]["human_box_integrity"])
        self.assertFalse(report["allow_cpu_research_experiment"])
        self.assertFalse(report["production_release"])

    def test_default_only_preflights_never_trains_or_downloads_weights(self):
        output = Path(self.temp.name) / "run"
        report = run_experiment(self.root, output)
        self.assertEqual(report["stage"], "BLOCKED")
        self.assertTrue((output / "preflight.json").exists())
        self.assertFalse((output / "basketball_research_only.pt").exists())
        self.assertFalse((output / "box_comparison.json").exists())
        self.assertFalse(report["production_release"])

    def test_explicit_execute_still_blocks_synthetic_before_any_torch_call(self):
        result = run_experiment(self.root, Path(self.temp.name) / "blocked",
                                execute=True)
        self.assertEqual(result["stage"], "BLOCKED")
        self.assertFalse(result["production_release"])

    def test_fake_box_review_does_not_overcome_synthetic_flag(self):
        report = self._manifest()
        for sample in report["samples"]:
            sample["quality_verified"] = True
            sample["quality_verified_by_id"] = 42
        self._replace_manifest(report)
        self.assertFalse(inspect_dataset(self.root)["allow_cpu_research_experiment"])

    def test_shuffled_split_or_forged_count_is_blocked(self):
        m = self._manifest()
        m["synthetic"] = False
        m["WARNING"] = "Manual audit placeholder"
        m["samples"][0]["split"] = "test"
        m["games"][0]["positive"] += 999
        self._replace_manifest(m)
        report = inspect_dataset(self.root)
        self.assertFalse(report["checks"]["human_box_integrity"])
        self.assertFalse(report["checks"]["declared_counts_match_frames"])
        self.assertFalse(report["allow_cpu_research_experiment"])

    def test_changed_image_hash_is_blocked(self):
        m = self._manifest()
        path = self.root / m["samples"][0]["image"]
        path.write_bytes(b"not-a-jpeg")
        report = inspect_dataset(self.root)
        self.assertFalse(report["checks"]["human_box_integrity"])
        self.assertTrue(report["format_error_examples"])

    def test_cross_partition_duplicate_image_is_blocked(self):
        m = self._manifest()
        a = next(x for x in m["samples"] if x["split"] == "train")
        b = next(x for x in m["samples"] if x["split"] == "test")
        new = (self.root / a["image"]).read_bytes()
        (self.root / b["image"]).write_bytes(new)
        import hashlib
        b["sha256_image"] = hashlib.sha256(new).hexdigest()
        self._replace_manifest(m)
        report = inspect_dataset(self.root)
        self.assertFalse(report["checks"]["no_cross_partition_identical_frames"])

    def test_unreviewed_label_and_bad_box_are_blocked(self):
        m = self._manifest()
        sample = m["samples"][0]
        sample["quality_verified"] = False
        self._replace_manifest(m)
        self.assertFalse(inspect_dataset(self.root)["checks"]["human_box_integrity"])
        sample["quality_verified"] = True
        (self.root / sample["label"]).write_text("0 NaN 0.5 0.5 0.2\n")
        self.assertFalse(inspect_dataset(self.root)["checks"]["human_box_integrity"])

    def test_no_existing_nonempty_output_overwrite(self):
        output = Path(self.temp.name) / "used"
        output.mkdir()
        (output / "important.txt").write_text("preserve me")
        with self.assertRaisesRegex(ValueError, "empty directory"):
            run_experiment(self.root, output)
        self.assertEqual((output / "important.txt").read_text(), "preserve me")

    def test_output_cannot_be_inside_dataset_or_its_parent(self):
        with self.assertRaisesRegex(ValueError, "separate output"):
            run_experiment(self.root, self.root / "results")
        with self.assertRaisesRegex(ValueError, "separate output"):
            run_experiment(self.root, Path(self.temp.name))

    def test_dry_run_with_authenticity_flag_in_unit_fixture_is_only_preflight(self):
        # Unit test of non-synthetic path logic, not evidence of real human labels.
        m = self._manifest()
        m["synthetic"] = False
        m["WARNING"] = "UNIT FIXTURE: fake pixels used to validate code only"
        self._replace_manifest(m)
        audit = inspect_dataset(self.root)
        self.assertTrue(audit["allow_cpu_research_experiment"])
        result = run_experiment(self.root, Path(self.temp.name) / "dry")
        self.assertEqual(result["stage"], "PREFLIGHT_ONLY")
        self.assertFalse(result["production_release"])
        self.assertFalse((Path(self.temp.name) / "dry" / "basketball_research_only.pt").exists())
