"""唯一 LEGO 后端的领域状态、输入交接与 CLI 回归。"""

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import open3d as o3d

from legolizer.cli import _parser, main
from legolizer.integrations.backend import run_strict_backend
from legolizer.pipeline import legoize_mesh
from legolizer.processing3d.volume import VoxelVolume


class StrictPipelineTests(unittest.TestCase):
    def test_partial_bundle_retains_failed_instruction_audit(self):
        volume = VoxelVolume(np.ones((2, 1, 1), dtype=bool), (8, 8, 9.6), (0, 0, 0))
        with TemporaryDirectory() as directory:
            output = Path(directory)

            def backend(input_path, bundle_dir, **options):
                values = np.load(input_path)
                np.testing.assert_array_equal(values, np.full((2, 1, 1), 4))
                self.assertEqual(options["support_mode"], "table")
                self.assertFalse(options["hollow"])
                (bundle_dir / "model").mkdir(parents=True)
                (bundle_dir / "model/model.mpd").write_text("0 fixture\n", encoding="utf-8")
                (bundle_dir / "instructions").mkdir()
                (bundle_dir / "bom").mkdir()
                (bundle_dir / "bom/bom.json").write_text(
                    json.dumps({"brick_count": 2}), encoding="utf-8")
                (bundle_dir / "bundle.json").write_text(json.dumps({
                    "status": "partial", "verdicts": {"buildable": True, "stable": True},
                    "stages": {"instructions": {"status": "partial"}},
                }), encoding="utf-8")
                (bundle_dir / "instructions/audit.json").write_text(json.dumps({
                    "input": {"step_count": 2},
                    "certification": {"valid": False, "earliest_failure": 2},
                }), encoding="utf-8")
                return {"exit_code": 3}

            with patch("legolizer.integrations.backend.run_legolization_bundle", side_effect=backend):
                report = run_strict_backend(volume, output)
            self.assertEqual(report["exit_code"], 3)
            self.assertEqual(report["brick_count"], 2)
            self.assertEqual(report["bundle_status"], "partial")
            self.assertTrue(report["buildable"])
            self.assertTrue(report["stable"])
            self.assertFalse(report["instruction_audit_valid"])
            self.assertEqual(report["instruction_earliest_failure"], 2)
            self.assertTrue(Path(report["model_path"]).is_file())

    def test_wrong_voxel_dimensions_do_not_start_backend(self):
        volume = VoxelVolume(np.ones((1, 1, 1), dtype=bool), (1, 1, 1), (0, 0, 0))
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch("legolizer.integrations.backend.run_legolization_bundle") as backend:
                with self.assertRaisesRegex(ValueError, "mm 体素"):
                    run_strict_backend(volume, output)
            backend.assert_not_called()
            self.assertFalse(output.exists())

    def test_mesh_uses_strict_backend_and_cli_preserves_partial_exit(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "input.ply"
            mesh = o3d.geometry.TriangleMesh.create_box(32, 16, 9.6)
            self.assertTrue(o3d.io.write_triangle_mesh(str(source), mesh))
            original = source.read_bytes()
            output = Path(directory) / "output"
            partial = {"exit_code": 3, "bundle_status": "partial", "buildable": True}
            with patch("legolizer.pipeline.run_strict_backend", return_value=partial) as backend:
                with redirect_stdout(io.StringIO()) as captured:
                    status = main(["legoize", str(source), str(output), "--target-width-studs", "4"])
            self.assertEqual(status, 3)
            report = json.loads(captured.getvalue())
            self.assertEqual(report["backend"], "legolization")
            self.assertEqual(report["legolization"]["bundle_status"], "partial")
            volume = backend.call_args.args[0]
            self.assertEqual(volume.voxel_size_mm, (8, 8, 9.6))
            self.assertEqual(int(volume.occupancy.sum()), report["voxel"]["occupied_cells"])
            self.assertEqual(source.read_bytes(), original)
            self.assertTrue((output / "source_occupancy.npz").is_file())
            with self.assertRaisesRegex(ValueError, "输出目录必须为空"):
                legoize_mesh(source, output, target_width_studs=4)

    def test_removed_commands_and_greedy_flag_are_rejected(self):
        for args in [["export-unity", "x"], ["visualize", "x"],
                     ["compare-legoization", "x", "y"],
                     ["legoize", "x", "y", "--backend", "greedy"]]:
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    _parser().parse_args(args)
                self.assertEqual(failure.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
