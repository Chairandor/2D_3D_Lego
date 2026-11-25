import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import open3d as o3d

from legolizer.processing3d.colors import TEXTURE_LDRAW_COLORS, quantize_rgb_to_ldraw
from legolizer.processing3d.texturing import bake_uv_atlas
from legolizer.processing3d.texture_voxels import color_from_uv
from legolizer.processing3d.voxel import point_cloud_to_voxel
from legolizer.integrations.legolization import (
    export_legolization_npy,
    run_legolization_bundle,
)
from legolizer.processing3d.volume import VoxelVolume


class ColorVoxelTests(unittest.TestCase):
    def test_palette_maps_primary_colors(self) -> None:
        codes = quantize_rgb_to_ldraw(
            np.asarray([[201, 26, 9], [242, 205, 55], [30, 90, 168]])
        )
        np.testing.assert_array_equal(codes, np.asarray([4, 14, 1]))

    def test_colored_point_cloud_to_voxel(self) -> None:
        cloud = o3d.geometry.PointCloud(
            o3d.utility.Vector3dVector(
                np.asarray([[0.1, 0.1, 0.1], [0.2, 0.2, 0.2], [8.1, 0.1, 0.1]])
            )
        )
        cloud.colors = o3d.utility.Vector3dVector(
            np.asarray([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        )

        volume = point_cloud_to_voxel(cloud, (8.0, 8.0, 9.6))

        self.assertEqual(volume.occupancy.shape, (2, 1, 1))
        self.assertEqual(int(volume.occupancy.sum()), 2)
        self.assertIsNotNone(volume.colors)
        self.assertEqual(int(volume.colors[0, 0, 0]), 4)
        self.assertEqual(int(volume.colors[1, 0, 0]), 1)

    def test_legolization_export_uses_minus_one_for_empty(self) -> None:
        volume = VoxelVolume(
            np.asarray([[[True]], [[False]]]),
            (8.0, 8.0, 9.6),
            (0.0, 0.0, 0.0),
            colors=np.asarray([[[14]], [[-1]]], dtype=np.int16),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = export_legolization_npy(volume, Path(directory) / "grid.npy")
            values = np.load(path)
        self.assertEqual(int(values[0, 0, 0]), 14)
        self.assertEqual(int(values[1, 0, 0]), -1)

    def test_baked_texture_matches_purple_on_surface_and_preserves_free_interior(self) -> None:
        mesh = o3d.geometry.TriangleMesh.create_box(24, 24, 28.8)
        mesh.compute_vertex_normals()
        mesh.paint_uniform_color(np.array([117, 101, 125]) / 255.)
        volume = VoxelVolume(np.ones((3, 3, 3), dtype=bool), (8., 8., 9.6), (0., 0., 0.))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = bake_uv_atlas(mesh, root / "atlas", resolution=64)
            sampled = color_from_uv(volume, np.eye(4), root / "atlas" / bundle["obj"],
                                    root / "atlas" / bundle["texture"], TEXTURE_LDRAW_COLORS, 0.)
            exported = np.load(export_legolization_npy(sampled, root / "grid.npy", preserve_unconstrained=True), allow_pickle=False)
            bundle_input = np.load(export_legolization_npy(sampled, root / "bundle_input.npy"), allow_pickle=False)
        self.assertEqual(int(sampled.colors[1, 1, 1]), -2)
        self.assertEqual(int(exported[1, 1, 1]), -2)
        self.assertEqual(np.count_nonzero(exported == 373), 26)
        self.assertEqual(int(bundle_input[1, 1, 1]), 4)
        np.testing.assert_array_equal(bundle_input[exported >= 0], exported[exported >= 0])
        self.assertEqual(int(sampled.occupancy.sum()), 27)

    @patch("legolizer.integrations.legolization.subprocess.run")
    def test_unbuildable_is_returned_as_domain_result(self, run) -> None:
        run.return_value.returncode = 2
        run.return_value.stdout = '{"status":"unbuildable","exit_code":2}'
        run.return_value.stderr = ""

        result = run_legolization_bundle("input.npy", "bundle", quality="direct")

        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["result"]["status"], "unbuildable")

    @patch("legolizer.integrations.legolization.subprocess.run")
    def test_solver_memory_failure_has_width_hint(self, run) -> None:
        run.return_value.returncode = 1
        run.return_value.stderr = "MemoryError: std::bad_alloc"
        with self.assertRaisesRegex(RuntimeError, "减小 LEGO 宽度") as error:
            run_legolization_bundle("input.npy", "bundle")
        self.assertIn("std::bad_alloc", str(error.exception.__cause__))


if __name__ == "__main__":
    unittest.main()
