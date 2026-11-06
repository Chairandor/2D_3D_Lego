import unittest

import numpy as np
import open3d as o3d

from legolizer.processing3d.voxel import mesh_to_voxel, voxel_to_mesh


class VoxelTests(unittest.TestCase):
    def test_box_voxel_and_marching_cubes(self) -> None:
        mesh = o3d.geometry.TriangleMesh.create_box(width=32.0, height=24.0, depth=19.2)
        volume = mesh_to_voxel(mesh, (8.0, 8.0, 9.6), fill="orthographic")
        self.assertGreater(int(volume.occupancy.sum()), 0)
        reconstructed = voxel_to_mesh(volume)
        self.assertGreater(len(reconstructed.vertices), 0)
        self.assertGreater(len(reconstructed.triangles), 0)


if __name__ == "__main__":
    unittest.main()
