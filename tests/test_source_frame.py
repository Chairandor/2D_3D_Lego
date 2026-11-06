"""Known-source orientation is opt-in and scale invariant."""

import json
import io
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import open3d as o3d

from legolizer.processing3d.source_frame import source_frame_rotation
from legolizer.cli import main
from legolizer.pipeline import legoize_mesh
from legolizer.processing3d.preview import preview_file


def _tilted_box() -> o3d.geometry.TriangleMesh:
    mesh = o3d.geometry.TriangleMesh.create_box(0.8, 1.2, 0.5)
    rotation = mesh.get_rotation_matrix_from_xyz((np.radians(14), np.radians(28), 0))
    mesh.rotate(rotation, center=(0, 0, 0))
    return mesh


class SourceFrameTests(unittest.TestCase):
    def test_tilted_box_is_level_and_rotation_is_proper(self) -> None:
        mesh = _tilted_box()
        transform, report = source_frame_rotation(mesh)
        self.assertEqual(report["alignment_status"], "planar_correction")
        self.assertAlmostEqual(np.linalg.det(transform[:3, :3]), 1.0, places=8)
        self.assertLess(report["planar_up_deviation_degrees"], 25)
        oriented = o3d.geometry.TriangleMesh(mesh)
        oriented.transform(transform)
        normals = np.asarray(oriented.triangle_normals)
        if not len(normals):
            oriented.compute_triangle_normals()
            normals = np.asarray(oriented.triangle_normals)
        self.assertGreater(float(np.max(np.abs(normals @ [0, 0, 1]))), 0.999)
        original_up = mesh.get_rotation_matrix_from_xyz((np.radians(14), np.radians(28), 0)) @ [0, 1, 0]
        original_front = mesh.get_rotation_matrix_from_xyz((np.radians(14), np.radians(28), 0)) @ [0, 0, -1]
        np.testing.assert_allclose(transform[:3, :3] @ original_up, [0, 0, 1], atol=1e-8)
        np.testing.assert_allclose(transform[:3, :3] @ original_front, [0, -1, 0], atol=1e-8)

    def test_scale_does_not_change_rotation(self) -> None:
        mesh = _tilted_box()
        scaled = o3d.geometry.TriangleMesh(mesh)
        scaled.scale(0.001, center=(0, 0, 0))
        first, first_report = source_frame_rotation(mesh)
        second, second_report = source_frame_rotation(scaled)
        np.testing.assert_allclose(first, second, atol=1e-10)
        self.assertEqual(first_report["alignment_status"], second_report["alignment_status"])

    def test_curved_mesh_uses_source_frame_without_planar_snap(self) -> None:
        transform, report = source_frame_rotation(o3d.geometry.TriangleMesh.create_sphere(1.0, 20))
        self.assertEqual(report["alignment_status"], "source_frame_only")
        np.testing.assert_allclose(transform[:3, :3] @ [0, 1, 0], [0, 0, 1])
        np.testing.assert_allclose(transform[:3, :3] @ [0, 0, -1], [0, -1, 0])

    @patch("legolizer.pipeline.run_strict_backend", return_value={"exit_code": 3, "bundle_status": "partial"})
    def test_preview_and_pipeline_record_provenance(self, strict) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "tilted.ply"
            output = Path(directory) / "output"
            self.assertTrue(o3d.io.write_triangle_mesh(str(source), _tilted_box()))
            info = preview_file(source, info_only=True, source_frame="sf3d")
            self.assertEqual(info["orientation"]["selection_source"], "source_frame")
            with redirect_stdout(io.StringIO()) as captured:
                self.assertEqual(main(["preview", str(source), "--source-frame", "sf3d", "--info"]), 0)
            self.assertEqual(json.loads(captured.getvalue())["source_frame"], "sf3d")
            report = legoize_mesh(
                source, output, source_frame="sf3d", target_width_studs=4,
            )
            transform = json.loads((output / "transform.json").read_text(encoding="utf-8"))
            self.assertEqual(report["orientation_selection_source"], "source_frame")
            self.assertEqual(transform["source_up_axis"], "y")
            self.assertEqual(report["source_up_axis"], "y")
            self.assertEqual(transform["orientation"]["alignment_status"], "planar_correction")
            self.assertEqual(report["backend"], "legolization")
            self.assertEqual(report["legolization"]["bundle_status"], "partial")
            self.assertGreater(int(strict.call_args.args[0].occupancy.sum()), 0)

    def test_incompatible_axis_does_not_create_output(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "box.ply"
            output = Path(directory) / "output"
            self.assertTrue(o3d.io.write_triangle_mesh(str(source), _tilted_box()))
            with self.assertRaisesRegex(ValueError, "不能同时使用"):
                legoize_mesh(source, output, source_frame="sf3d", source_up_axis="auto")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
