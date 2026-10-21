"""V1.6: fail-closed full-game admissions, never mock up production evidence."""
import hashlib
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from highlights.vision.real_match_gate_v16 import audit


class V16RealMatchGateTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sample(self):
        rows = []
        for i in range(3):
            game_id = f"match_{i}"
            name = f"match{i}.mp4"
            data = (f"not-a-real-video-{i}-".encode() * 10)
            (self.root / name).write_bytes(data)
            times = [float(30+j*150) for j in range(20)]
            truth = {
                "schema_version": 1, "game_id": game_id,
                "video_duration_seconds": 3600,
                "complete_within_scope": True,
                "events": [{"type": "shot_attempt", "time_seconds": t} for t in times],
            }
            proposal = {
                "schema_version": 2, "game_id": game_id,
                "video_duration_seconds": 3600,
                "analysis": {"incomplete_scan": False,
                             "analyzed_until_seconds": 3600,
                             "coverage_ratio": 1.0, "elapsed_seconds": 5},
                "candidates": [{"start_seconds": t - 1, "end_seconds": t+1} for t in times],
            }
            (self.root / f"truth{i}.json").write_text(json.dumps(truth))
            (self.root / f"proposal{i}.json").write_text(json.dumps(proposal))
            rows.append({
                "id": game_id, "video_file": name,
                "video_sha256": hashlib.sha256(data).hexdigest(),
                "truth_file": f"truth{i}.json", "proposals_file": f"proposal{i}.json",
                "synthetic": False, "looped": False,
                "continuous_unedited_full_match": True,
                "rights_to_process_and_store": True,
                "license_evidence_reference": f"private-permission-note-{i}",
                "independent_of_training_and_tuning": True,
                "human_reviewers": [
                    {"reviewer_id": f"r_{i}_A",
                     "verified_complete_shot_attempt_labels": True,
                     "review_evidence_reference": f"review-a-{i}"},
                    {"reviewer_id": f"r_{i}_B",
                     "verified_complete_shot_attempt_labels": True,
                     "review_evidence_reference": f"review-b-{i}"},
                ]
            })
        return {"schema_version": 1, "games": rows}

    def audit_mock_duration(self, manifest):
        # Unit-only! Pretend bytes are 3600s to test audit decisions;
        # no assertion here is a real long-match acceptance.
        return audit(manifest, self.root, media_probe=lambda _: 3600)

    def test_three_attested_metadata_rows_are_only_eligible_for_human_review(self):
        result = self.audit_mock_duration(self.sample())
        self.assertEqual(result["outcome"], "ELIGIBLE_FOR_HUMAN_RELEASE_REVIEW")
        self.assertFalse(result["independent_source_and_rights_validation_completed"])
        self.assertFalse(result["basketball_made_shot_accuracy_measured"])
        self.assertEqual(result["aggregate"]["annotated_shot_attempts"], 60)

    def test_looped_recording_is_blocked_despite_fake_perfect_labels(self):
        manifest = self.sample()
        manifest["games"][0]["looped"] = True
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")

    def test_incomplete_scan_is_blocked(self):
        manifest = self.sample()
        p = self.root / "proposal0.json"
        proposal = json.loads(p.read_text())
        proposal["analysis"]["coverage_ratio"] = .5
        proposal["analysis"]["incomplete_scan"] = True
        p.write_text(json.dumps(proposal))
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")

    def test_missing_second_human_review_blocks(self):
        manifest = self.sample()
        manifest["games"][1]["human_reviewers"] = manifest["games"][1]["human_reviewers"][:1]
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")

    def test_rights_unattested_blocks(self):
        manifest = self.sample()
        manifest["games"][0]["rights_to_process_and_store"] = False
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")

    def test_missing_video_file_or_wrong_hash_blocks(self):
        manifest = self.sample()
        manifest["games"][0]["video_sha256"] = "0" * 64
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")
        manifest = self.sample()
        manifest["games"][0]["video_file"] = "../exfiltration.mp4"
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")

    def test_same_video_reused_as_different_game_blocked(self):
        manifest = self.sample()
        for name in ("video_file", "video_sha256"):
            manifest["games"][1][name] = manifest["games"][0][name]
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")

    def test_less_than_three_games_not_eligible(self):
        manifest = self.sample()
        manifest["games"] = manifest["games"][:2]
        self.assertEqual(self.audit_mock_duration(manifest)["outcome"], "BLOCKED")
