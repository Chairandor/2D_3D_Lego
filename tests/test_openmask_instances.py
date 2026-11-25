"""Check instance extraction against the exact scene vertex positions and colours."""

import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
import open3d as o3d

from legolizer.processing3d.openmask_instances import extract_instance, inspect_instances


class OpenMaskInstancesTests(unittest.TestCase):
    def test_mask_columns_preserve_source_vertex_order_and_rgb(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            points = np.random.default_rng(4).random((30, 3))
            colors = np.arange(90).reshape(30, 3) / 255.
            cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
            cloud.colors = o3d.utility.Vector3dVector(colors)
            scene = root / "scene.ply"
            self.assertTrue(o3d.io.write_point_cloud(str(scene), cloud))
            masks = np.zeros((30, 3), dtype=bool)
            masks[::2, 1] = True
            masks[1::3, 2] = True
            archive = root / "masks.npz"
            np.savez_compressed(archive, masks=masks, scene_sha256=hashlib.sha256(scene.read_bytes()).hexdigest())
            self.assertEqual(inspect_instances(scene, archive)["instances"], [{"id": 1, "points": 15}, {"id": 2, "points": 10}])
            self.assertEqual(inspect_instances(scene, archive, 1)["instances"], [{"id": 2, "points": 10}])
            self.assertEqual(inspect_instances(scene, archive, 3)["instances"], [])
            result = extract_instance(scene, archive, 1, root / "instance.ply")
            selected = o3d.io.read_point_cloud(result["path"])
            np.testing.assert_array_equal(np.asarray(selected.points), points[::2])
            np.testing.assert_allclose(np.asarray(selected.colors), colors[::2])
            wrong = root / "wrong.npz"
            np.savez_compressed(wrong, masks=masks, scene_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                inspect_instances(scene, wrong)
