"""Generic regression tests for the standalone orientation module."""

from __future__ import annotations

import unittest

import numpy as np
import trimesh

from legolizer.processing3d.source_orientation import _input_view_score, upright_mesh


class OrientationTests(unittest.TestCase):
    def test_camera_tie_break_preserves_input_view(self) -> None:
        rotations = [
            trimesh.transformations.rotation_matrix(
                np.radians(degrees), [0, 1, 0]
            )[:3, :3]
            for degrees in (0, 90, 180, 270)
        ]
        scores = [_input_view_score(rotation, np.eye(3)) for rotation in rotations]
        self.assertEqual(int(np.argmax(scores)), 0)

    def test_monochrome_object_uses_source_up_and_camera(self) -> None:
        mesh = trimesh.creation.box(extents=(0.8, 1.2, 0.5))
        tilt = trimesh.transformations.rotation_matrix(np.radians(14), [1, 0, 0])
        yaw = trimesh.transformations.rotation_matrix(np.radians(28), [0, 1, 0])
        mesh.apply_transform(yaw @ tilt)
        original = np.asarray(mesh.vertices).copy()

        result = upright_mesh(mesh)
        aligned = np.asarray(result.vertices)
        rotation, *_ = np.linalg.lstsq(
            original - original.mean(axis=0),
            aligned - aligned.mean(axis=0),
            rcond=None,
        )
        self.assertGreater(float(rotation[1, 1]), 0.9)
        source_front = np.array([0.0, 0.0, -1.0]) @ rotation
        self.assertLess(float(source_front[2]), -0.5)
        self.assertAlmostEqual(float(result.bounds[0][1]), 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
