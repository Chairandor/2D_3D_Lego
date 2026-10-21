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

    def test_only_non_lego_models_receive_browser_previews(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            for relative in (
                "hunyuan/mesh_shape_review_clean.glb",
                "unitex/textured_mesh.glb",
                "oriented/mesh_upright.glb",
                "oriented/lego_input.ply",
                "lego-16/model.mpd",
            ):
                target = run_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"test")
            artifacts = pipeline_ui.artifact_inventory(run_dir)
            previews = {
                item["relative"]: item.get("preview_format")
                for item in artifacts
            }
            self.assertEqual(previews["hunyuan/mesh_shape_review_clean.glb"], "glb")
            self.assertEqual(previews["unitex/textured_mesh.glb"], "glb")
            self.assertEqual(previews["oriented/mesh_upright.glb"], "glb")
            self.assertEqual(previews["oriented/lego_input.ply"], "ply")
            self.assertIsNone(previews["lego-16/model.mpd"])


if __name__ == "__main__":
    unittest.main()
