"""Synthetic checks for camera-search silhouette diagnostics."""

import unittest

import numpy as np
from PIL import Image
import trimesh

from shape_qa import analyze, silhouette


class ShapeQaTests(unittest.TestCase):
    def test_exact_box_silhouette_scores_high(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 1.4, 0.6))
        target = silhouette(mesh, yaw=0, elevation=0)
        rgba = np.full((256, 256, 4), 255, dtype=np.uint8)
        rgba[:, :, 3] = target.astype(np.uint8) * 255

        report, overlay = analyze(mesh, Image.fromarray(rgba, "RGBA"))

        self.assertGreater(report["silhouette_iou"], 0.95)
        self.assertEqual(overlay.shape, (256, 256, 3))


if __name__ == "__main__":
    unittest.main()
