"""Synthetic regression checks for shape-fragment cleanup."""

import unittest

import numpy as np
import trimesh

from shape_component_cleanup import remove_negligible_components


class ShapeComponentCleanupTests(unittest.TestCase):
    def test_removes_tiny_fragment_but_keeps_main_shape(self) -> None:
        main = trimesh.creation.box()
        for _ in range(3):
            main = main.subdivide()
        fragment = trimesh.Trimesh(
            vertices=np.array([[3.0, 0.0, 0.0], [3.1, 0.0, 0.0], [3.0, 0.1, 0.0]]),
            faces=[[0, 1, 2]],
            process=False,
        )
        mesh = trimesh.util.concatenate((main, fragment))

        cleaned, report = remove_negligible_components(mesh)

        self.assertEqual(report["removed"], 1)
        self.assertEqual(len(cleaned.faces), len(main.faces))
        self.assertTrue(cleaned.is_watertight)

    def test_preserves_two_substantial_disconnected_parts(self) -> None:
        left = trimesh.creation.box()
        right = trimesh.creation.box()
        right.apply_translation([3.0, 0.0, 0.0])
        mesh = trimesh.util.concatenate((left, right))

        cleaned, report = remove_negligible_components(mesh)

        self.assertEqual(report["status"], "review_needed")
        self.assertEqual(len(cleaned.faces), len(mesh.faces))


if __name__ == "__main__":
    unittest.main()
