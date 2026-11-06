from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import open3d as o3d

from legolizer.processing3d.orientation import (
    _clipped_bottom_projection,
    orientation_matrix,
    score_mesh_orientations,
)


def _read_model_mesh(path: Path) -> o3d.geometry.TriangleMesh:
    model = o3d.io.read_triangle_model(str(path))
    combined = o3d.geometry.TriangleMesh()
    for entry in model.meshes:
        combined += entry.mesh
    return combined


def _make_square_frustum() -> o3d.geometry.TriangleMesh:
    vertices = np.asarray(
        [
            (-2.0, -2.0, 0.0),
            (2.0, -2.0, 0.0),
            (2.0, 2.0, 0.0),
            (-2.0, 2.0, 0.0),
            (-0.5, -0.5, 2.0),
            (0.5, -0.5, 2.0),
            (0.5, 0.5, 2.0),
            (-0.5, 0.5, 2.0),
        ],
        dtype=np.float64,
    )
    triangles = np.asarray(
        [
            (0, 2, 1), (0, 3, 2),  # broad bottom
            (4, 5, 6), (4, 6, 7),  # narrow top
            (0, 1, 5), (0, 5, 4),
            (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6),
            (3, 0, 4), (3, 4, 7),
        ],
        dtype=np.int32,
    )
    return o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(vertices),
        o3d.utility.Vector3iVector(triangles),
    )


class OrientationTests(unittest.TestCase):
    def test_bottom_slab_clips_partial_triangles(self) -> None:
        vertices = np.asarray([[(0.0, 0.0, 0.0), (2.0, 0.0, 2.0), (0.0, 2.0, 2.0)]])
        area, points = _clipped_bottom_projection(
            vertices, vertices[:, :, 2], 1.0, [0, 1]
        )
        self.assertAlmostEqual(area, 0.5)
        self.assertEqual(len(points), 3)

        vertices = np.asarray([[(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 2.0)]])
        area, points = _clipped_bottom_projection(
            vertices, vertices[:, :, 2], 1.0, [0, 1]
        )
        self.assertAlmostEqual(area, 1.5)
        self.assertEqual(len(points), 4)

    def test_contact_proxy_is_invariant_to_planar_subdivision(self) -> None:
        for mesh in (o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0),
                     _make_square_frustum()):
            subdivided = mesh.subdivide_midpoint(number_of_iterations=1)
            original = score_mesh_orientations(mesh)
            refined = score_mesh_orientations(subdivided)
            original_by_axis = {item["axis"]: item for item in original["candidates"]}
            refined_by_axis = {item["axis"]: item for item in refined["candidates"]}
            for axis in ("x", "-x", "y", "-y", "z", "-z"):
                with self.subTest(axis=axis, triangles=len(mesh.triangles)):
                    self.assertAlmostEqual(
                        original_by_axis[axis]["contact_area_ratio"],
                        refined_by_axis[axis]["contact_area_ratio"],
                        places=10,
                    )
                    self.assertAlmostEqual(
                        original_by_axis[axis]["support_hull_area_input_units_squared"],
                        refined_by_axis[axis]["support_hull_area_input_units_squared"],
                        places=10,
                    )

    def test_support_proxy_excludes_symmetry(self) -> None:
        result = score_mesh_orientations(_make_square_frustum())
        for item in result["candidates"]:
            expected = 0.65 * item["contact_area_ratio"] + 0.35 * item["support_score"]
            self.assertAlmostEqual(item["support_proxy_score"], expected)
            self.assertEqual(item["stability_score"], item["support_proxy_score"])

    def test_orientation_matrices_map_signed_axis_to_positive_z(self) -> None:
        unit_axes = {
            "x": np.asarray((1.0, 0.0, 0.0)),
            "-x": np.asarray((-1.0, 0.0, 0.0)),
            "y": np.asarray((0.0, 1.0, 0.0)),
            "-y": np.asarray((0.0, -1.0, 0.0)),
            "z": np.asarray((0.0, 0.0, 1.0)),
            "-z": np.asarray((0.0, 0.0, -1.0)),
        }
        for axis, vector in unit_axes.items():
            with self.subTest(axis=axis):
                transform = orientation_matrix(axis)
                rotation = transform[:3, :3]
                np.testing.assert_allclose(rotation @ vector, (0.0, 0.0, 1.0), atol=1e-12)
                np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
                self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.0)
                np.testing.assert_array_equal(transform[3], (0.0, 0.0, 0.0, 1.0))

    def test_y_and_z_matrices_match_project_convention(self) -> None:
        expected_y = np.asarray(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, -1.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
        np.testing.assert_array_equal(orientation_matrix("y"), expected_y)
        np.testing.assert_array_equal(orientation_matrix("z"), np.eye(4))

    def test_invalid_axis_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            orientation_matrix("up")

    def test_closed_cube_contact_ratio_and_symmetry_are_reported(self) -> None:
        mesh = o3d.geometry.TriangleMesh.create_box(1.0, 1.0, 1.0)
        result = score_mesh_orientations(mesh)
        positive_z = next(item for item in result["candidates"] if item["axis"] == "z")
        self.assertAlmostEqual(positive_z["contact_area_ratio"], 1.0 / 6.0, places=7)
        self.assertGreater(positive_z["center_of_mass_margin"], 0.2)
        self.assertEqual(positive_z["center_of_mass_source"], "closed_mesh_volume_centroid")
        self.assertGreater(positive_z["symmetry_score"], 0.9)
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["selected_axis"])
        self.assertEqual(len(result["candidates"]), 6)

    def test_long_rectangular_box_separates_display_from_physical_stability(self) -> None:
        mesh = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 4.0)
        result = score_mesh_orientations(mesh)
        stable = sorted(result["candidates"], key=lambda item: -item["stability_score"])
        self.assertEqual({item["axis"] for item in stable[:2]}, {"x", "-x"})
        self.assertEqual({item["axis"] for item in result["candidates"][:2]}, {"z", "-z"})
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["selected_axis"])

    def test_repository_knot_prefers_x_axis_family(self) -> None:
        path = Path(__file__).resolve().parents[1] / "datasets/others/knot.stl"
        if not path.is_file():
            self.skipTest("仓库 Knot 实验网格不存在")
        result = score_mesh_orientations(o3d.io.read_triangle_mesh(str(path)))
        self.assertIn(result["selected_axis"], {"x", "-x"}, result["reason"])
        self.assertGreaterEqual(
            result["axis_family_confidence"], result["ambiguity_gap_threshold"]
        )

    def test_cached_open3d_sword_uses_slender_display_exception(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "datasets/open3d_orientation/extract/SwordModel/UV.obj"
        )
        if not path.is_file():
            self.skipTest("Open3D Sword 单物体缓存不存在")
        result = score_mesh_orientations(_read_model_mesh(path))
        self.assertEqual(result["selected_axis"], "z", result["reason"])
        self.assertTrue(result["slender_contact_exception_used"])

    def test_cached_open3d_crate_stays_ambiguous(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "datasets/open3d_orientation/extract/CrateModel/crate.obj"
        )
        if not path.is_file():
            self.skipTest("Open3D Crate 单物体缓存不存在")
        result = score_mesh_orientations(_read_model_mesh(path))
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["selected_axis"])

    def test_square_frustum_selects_the_broad_supported_end_as_bottom(self) -> None:
        result = score_mesh_orientations(_make_square_frustum())
        self.assertFalse(result["ambiguous"], result["reason"])
        self.assertEqual(result["selected_axis"], "z")
        self.assertGreater(
            result["axis_family_confidence"], result["ambiguity_gap_threshold"]
        )
        self.assertEqual(result["candidates"][0]["axis"], "z")

    def test_rotated_frustum_selects_y_up(self) -> None:
        mesh = _make_square_frustum()
        mesh.transform(np.linalg.inv(orientation_matrix("y")))
        result = score_mesh_orientations(mesh)
        self.assertEqual(result["selected_axis"], "y", result["reason"])

    def test_open_mesh_uses_labeled_surface_proxy_instead_of_claiming_exact_com(self) -> None:
        mesh = o3d.geometry.TriangleMesh.create_box(1.0, 2.0, 3.0)
        mesh.remove_triangles_by_index(
            [triangle for triangle in range(len(mesh.triangles)) if triangle in (2, 3)]
        )
        mesh.remove_unreferenced_vertices()
        self.assertFalse(mesh.is_watertight())
        result = score_mesh_orientations(mesh)
        self.assertEqual(
            result["center_of_mass_source"],
            "surface_area_centroid_proxy_not_volume_center_of_mass",
        )
        self.assertTrue(
            all(
                item["center_of_mass_source"] == result["center_of_mass_source"]
                for item in result["candidates"]
            )
        )

    def test_degenerate_mesh_returns_ambiguous_result(self) -> None:
        mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(np.asarray(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)))),
            o3d.utility.Vector3iVector(np.asarray(((0, 1, 2),), dtype=np.int32)),
        )
        result = score_mesh_orientations(mesh)
        self.assertTrue(result["ambiguous"])
        self.assertIsNone(result["selected_axis"])
        self.assertEqual(len(result["candidates"]), 6)

    def test_results_are_json_serializable_and_scale_invariant(self) -> None:
        mesh = _make_square_frustum()
        original = score_mesh_orientations(mesh)
        scaled = o3d.geometry.TriangleMesh(mesh)
        scaled.scale(1000.0, center=(0.0, 0.0, 0.0))
        scaled_result = score_mesh_orientations(scaled)
        json.dumps(original)
        self.assertEqual(original["selected_axis"], scaled_result["selected_axis"])
        np.testing.assert_allclose(
            [item["score"] for item in original["candidates"]],
            [item["score"] for item in scaled_result["candidates"]],
            rtol=1e-10,
            atol=1e-10,
        )

    def test_invalid_mesh_input_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            score_mesh_orientations(object())  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "顶点"):
            score_mesh_orientations(o3d.geometry.TriangleMesh())

        vertices = np.asarray(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
        vertices[1, 0] = np.nan
        mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(vertices),
            o3d.utility.Vector3iVector(np.asarray(((0, 1, 2),), dtype=np.int32)),
        )
        with self.assertRaisesRegex(ValueError, "NaN"):
            score_mesh_orientations(mesh)


if __name__ == "__main__":
    unittest.main()
