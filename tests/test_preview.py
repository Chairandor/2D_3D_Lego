import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import open3d as o3d

from legolizer.processing3d.mesh import orient_mesh_z_up
from legolizer.processing3d.volume import VoxelVolume
from legolizer.processing3d.preview import voxel_surface_mesh
from legolizer.processing3d.preview import preview_file
from legolizer.pipeline import legoize_mesh


class PreviewTests(unittest.TestCase):
    def test_y_up_rotation_points_y_to_z(self) -> None:
        source = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0)
        oriented, transform = orient_mesh_z_up(source, "y")
        np.testing.assert_allclose(transform[:3, :3] @ [0, 1, 0], [0, 0, 1])
        np.testing.assert_allclose(oriented.get_axis_aligned_bounding_box().get_extent(), [1, 3, 2])

    def test_negative_x_up_rotation_points_to_z(self) -> None:
        source = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0)
        _, transform = orient_mesh_z_up(source, "-x")
        np.testing.assert_allclose(transform[:3, :3] @ [-1, 0, 0], [0, 0, 1])
        np.testing.assert_allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3))
        self.assertAlmostEqual(np.linalg.det(transform[:3, :3]), 1.0)

    def test_preview_auto_info_exposes_candidate_scores(self) -> None:
        with TemporaryDirectory() as directory:
            mesh_path = Path(directory) / "box.ply"
            mesh = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0)
            self.assertTrue(o3d.io.write_triangle_mesh(str(mesh_path), mesh))
            info = preview_file(mesh_path, source_up_axis="auto", info_only=True)
            self.assertEqual(info["type"], "mesh")
            self.assertEqual(len(info["orientation"]["candidates"]), 6)

    def test_legoize_auto_ambiguous_stops_before_creating_output(self) -> None:
        with TemporaryDirectory() as directory:
            mesh_path = Path(directory) / "box.ply"
            output = Path(directory) / "output"
            mesh = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0)
            self.assertTrue(o3d.io.write_triangle_mesh(str(mesh_path), mesh))
            ambiguous = {
                "selected_axis": None,
                "confidence": 0.0,
                "ambiguous": True,
                "candidates": [
                    {"axis": "z", "score": 0.5},
                    {"axis": "-z", "score": 0.5},
                ],
                "reason": "对称体缺少方向证据",
            }
            with patch("legolizer.pipeline.score_mesh_orientations", return_value=ambiguous):
                with self.assertRaisesRegex(ValueError, "无法可靠判断"):
                    legoize_mesh(mesh_path, output, source_up_axis="auto")
            self.assertFalse(output.exists())

    @patch("legolizer.pipeline.run_strict_backend", return_value={"exit_code": 3, "bundle_status": "partial"})
    def test_manual_up_axis_records_human_choice(self, strict) -> None:
        with TemporaryDirectory() as directory:
            mesh_path = Path(directory) / "box.ply"
            output = Path(directory) / "manual_output"
            mesh = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0)
            self.assertTrue(o3d.io.write_triangle_mesh(str(mesh_path), mesh))
            report = legoize_mesh(
                mesh_path, output, source_up_axis="z", target_width_studs=4,
            )
            transform = json.loads((output / "transform.json").read_text(encoding="utf-8"))
            self.assertEqual(report["orientation_selection_source"], "user_manual")
            self.assertEqual(transform["orientation_selection_source"], "user_manual")
            self.assertEqual(report["resolved_up_axis"], "z")

    def test_two_adjacent_voxels_only_draw_outer_faces(self) -> None:
        volume = VoxelVolume(
            np.ones((2, 1, 1), dtype=bool),
            (8.0, 8.0, 9.6),
            (10.0, 20.0, 30.0),
        )
        mesh = voxel_surface_mesh(volume)
        self.assertEqual(len(mesh.triangles), 20)
        np.testing.assert_allclose(mesh.get_min_bound(), [10.0, 20.0, 30.0])
        np.testing.assert_allclose(mesh.get_max_bound(), [26.0, 28.0, 39.6])


if __name__ == "__main__":
    unittest.main()
