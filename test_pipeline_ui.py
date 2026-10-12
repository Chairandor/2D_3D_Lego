from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pipeline_ui


class PipelineUITests(unittest.TestCase):
    def test_build_command_preserves_review_approvals(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            root = Path(temporary)
            image = root / "input.png"
            image.write_bytes(b"png")
            run_dir = root / "run"
            command, selected, output = pipeline_ui.build_command(
                {
                    "image_path": str(image),
                    "output_dir": str(run_dir),
                    "accept_preprocess": True,
                    "accept_geometry": True,
                    "accept_unitex": False,
                    "build_lego": False,
                    "projection": "ltm",
                    "unitex_gpu": 1,
                    "target_studs": 24,
                }
            )
            self.assertEqual(selected, image.resolve())
            self.assertEqual(output, run_dir.resolve())
            self.assertIn("--accept-preprocess", command)
            self.assertIn("--accept-geometry", command)
            self.assertNotIn("--accept-unitex", command)
            self.assertNotIn("--build-lego", command)
            self.assertEqual(command[command.index("--unitex-gpu") + 1], "1")

    def test_artifact_inventory_only_lists_existing_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            target = run_dir / "unitex" / "mv_rgb.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"preview")
            artifacts = pipeline_ui.artifact_inventory(run_dir)
            self.assertEqual([item["relative"] for item in artifacts], ["unitex/mv_rgb.png"])


if __name__ == "__main__":
    unittest.main()
