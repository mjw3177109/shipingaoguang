"""The optional learned-CPU CLI is tested without installing torch."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase
from highlights.vision.cpu_pretrained import main


class PretrainedCpuCliTests(SimpleTestCase):
    def test_roi_config_is_validated_and_sent_to_optional_detector(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Path(folder)
            config = d / "hoops.json"
            hoops = [{"x":.24,"y":.37,"w":.08,"h":.1}]
            config.write_text(json.dumps({
                "game_id":12, "video_duration_seconds":5.767,
                "hoops":hoops,
            }))
            output = d / "proposals.json"
            video = d / "test.webm"
            video.write_bytes(b"v06 genuine local test-file bytes")
            mock_result = {
                "schema_version":2, "video_duration_seconds":5.767,
                "analysis":{"ball_detections":0,"hoop_roi_enabled":True},
                "hoops":hoops,"observations":[],"candidates":[],
            }
            with patch("highlights.vision.cpu_pretrained.scan_pretrained_cpu",
                       return_value=mock_result) as detector:
                main(["--video",str(video),"--config",str(config),
                      "--out",str(output),"--tiles","4"])
            self.assertTrue(output.exists())
            saved = json.loads(output.read_text())
            self.assertEqual(saved["game_id"],12)
            self.assertEqual(len(saved["video_quick_fingerprint_sha256"]),64)
            self.assertEqual(saved["candidates"],[])
            self.assertEqual(detector.call_args.kwargs["hoops"],hoops)
            self.assertEqual(detector.call_args.kwargs["tiles"],4)
            self.assertFalse(detector.call_args.kwargs["allow_research_checkpoint"])

    def test_invalid_empty_roi_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            d=Path(folder)
            config=d/"empty.json"
            config.write_text('{"hoops":[]}')
            with self.assertRaisesRegex(ValueError,"at least one hoop"):
                main(["--video","fake.webm","--out",str(d/"out.json"),
                      "--config",str(config)])

    def test_explicit_research_checkpoint_flag_is_forwarded(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/"out.json"
            video=Path(folder)/"test.webm"
            video.write_bytes(b"v06 separate model flag test source")
            fake={"schema_version":2,"video_duration_seconds":5,
                  "analysis":{"checkpoint_used":True},"candidates":[]}
            with patch("highlights.vision.cpu_pretrained.scan_pretrained_cpu",
                       return_value=fake) as scanner:
                main(["--video",str(video),"--checkpoint","tiny.pt",
                      "--allow-research-checkpoint","--out",str(output)])
            self.assertEqual(scanner.call_args.kwargs["checkpoint_path"],"tiny.pt")
            self.assertTrue(scanner.call_args.kwargs["allow_research_checkpoint"])
