"""Lightweight orchestration tests; no model, SSH, or LEGO work is run."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import pipeline_2d_to_lego as pipeline


ROOT = Path(__file__).resolve().parent


class HunyuanUniTEXPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.work = Path(self.temp.name)
        self.image = self.work / "input.png"
        self.shape = self.work / "shape.glb"
        self.image.write_bytes(b"image")
        self.shape.write_bytes(b"shape")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _convert(self, _source: Path, ply: Path, **_kwargs) -> None:
        ply.parent.mkdir(parents=True, exist_ok=True)
        ply.write_bytes(b"ply")
        ply.with_name("mesh_upright.glb").write_bytes(b"upright")

    def test_valid_transparent_input_bypasses_segmentation(self) -> None:
        transparent = self.work / "transparent.png"
        image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((24, 18, 104, 112), fill=(240, 180, 40, 255))
        image.save(transparent)
        output = self.work / "prepared"
        with patch.object(pipeline, "prepare_foreground_semantic") as segment:
            foreground, report = pipeline.prepare_foreground_auto(transparent, output)
        segment.assert_not_called()
        self.assertEqual(report["method"], "transparent-input-passthrough")
        self.assertTrue(report["segmentation_skipped"])
        self.assertFalse(report["needs_review"])
        self.assertTrue(foreground.is_file())

    def test_opaque_rgba_is_not_treated_as_transparent_input(self) -> None:
        opaque = self.work / "opaque.png"
        Image.new("RGBA", (64, 64), (20, 30, 40, 255)).save(opaque)
        report = pipeline.inspect_transparent_input(opaque)
        self.assertFalse(report["accepted"])

    def test_existing_textured_mesh_skips_generation_and_stops_before_lego(self) -> None:
        textured = self.work / "textured.glb"
        textured.write_bytes(b"glb")
        run_dir = self.work / "run"
        argv = ["pipeline", "--from-textured", str(textured), "-o", str(run_dir)]
        with (
            patch.object(sys, "argv", argv),
            patch.object(pipeline, "convert_via_lego_python", side_effect=self._convert),
            patch.object(pipeline, "run_unitex_remote") as unitex,
            patch.object(pipeline, "reconstruct_hunyuan") as hunyuan,
        ):
            self.assertEqual(pipeline.main(), 0)
        unitex.assert_not_called()
        hunyuan.assert_not_called()
        state = json.loads((run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stage"], "oriented_review")

    def test_geometry_and_unitex_review_gates_are_ordered(self) -> None:
        run_dir = self.work / "run"
        argv = [
            "pipeline",
            str(self.image),
            "--from-shape",
            str(self.shape),
            "--preprocess-mode",
            "off",
            "--accept-geometry",
            "-o",
            str(run_dir),
        ]

        def fake_unitex(_reference: Path, _shape: Path, output: Path, **kwargs):
            self.assertEqual(kwargs["phase"], "stage1")
            output.mkdir(parents=True, exist_ok=True)
            (output / "mv_rgb.png").write_bytes(b"review")
            return None

        with (
            patch.object(sys, "argv", argv),
            patch.object(pipeline, "run_unitex_remote", side_effect=fake_unitex) as unitex,
            patch.object(pipeline, "convert_via_lego_python") as convert,
        ):
            self.assertEqual(pipeline.main(), 2)
        self.assertEqual(unitex.call_count, 1)
        convert.assert_not_called()
        state = json.loads((run_dir / "pipeline_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stage"], "unitex_review")

    def test_approved_stages_project_then_orient_but_do_not_build(self) -> None:
        run_dir = self.work / "run"
        argv = [
            "pipeline",
            str(self.image),
            "--from-shape",
            str(self.shape),
            "--preprocess-mode",
            "off",
            "--accept-geometry",
            "--accept-unitex",
            "-o",
            str(run_dir),
        ]
        phases: list[str] = []

        def fake_unitex(_reference: Path, _shape: Path, output: Path, **kwargs):
            phase = kwargs["phase"]
            phases.append(phase)
            output.mkdir(parents=True, exist_ok=True)
            if phase == "stage1":
                (output / "mv_rgb.png").write_bytes(b"review")
                return None
            textured = output / "textured_mesh.glb"
            textured.write_bytes(b"glb")
            return textured

        with (
            patch.object(sys, "argv", argv),
            patch.object(pipeline, "run_unitex_remote", side_effect=fake_unitex),
            patch.object(pipeline, "convert_via_lego_python", side_effect=self._convert),
            patch.object(pipeline, "run_legolization_build") as lego,
        ):
            self.assertEqual(pipeline.main(), 0)
        self.assertEqual(phases, ["stage1", "project"])
        lego.assert_not_called()
        self.assertTrue((run_dir / "oriented" / "mesh_upright.glb").is_file())


if __name__ == "__main__":
    unittest.main()
