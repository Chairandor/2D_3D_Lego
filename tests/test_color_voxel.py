import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import open3d as o3d

from legolizer.processing3d.colors import quantize_rgb_to_ldraw
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

    @patch("legolizer.integrations.legolization.subprocess.run")
    def test_unbuildable_is_returned_as_domain_result(self, run) -> None:
        run.return_value.returncode = 2
        run.return_value.stdout = '{"status":"unbuildable","exit_code":2}'
        run.return_value.stderr = ""

        result = run_legolization_bundle("input.npy", "bundle", quality="direct")

        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["result"]["status"], "unbuildable")


if __name__ == "__main__":
    unittest.main()
