import unittest

import numpy as np
from PIL import Image
import trimesh

from colorize_shape_from_image import (
    enclosed_dark_markings,
    normalized_rgba,
    projected_vertex_colours,
)


class ShapeColourProjectionTests(unittest.TestCase):
    def test_normalization_preserves_hard_rectangular_silhouette(self):
        data = np.zeros((100, 160, 4), dtype=np.uint8)
        data[20:80, 30:130] = [200, 50, 20, 255]
        normalized = normalized_rgba(Image.fromarray(data, "RGBA"), size=256)
        self.assertTrue(np.any(normalized[:, :, 3] >= 128))
        colours = normalized[normalized[:, :, 3] >= 250, :3]
        self.assertTrue(np.all(np.median(colours, axis=0) == [200, 50, 20]))

    def test_box_gets_complete_opaque_vertex_colours(self):
        image = np.zeros((256, 256, 4), dtype=np.uint8)
        image[20:236, 20:236] = [220, 40, 30, 255]
        mesh = trimesh.creation.box()
        colours, report = projected_vertex_colours(mesh, image, 0, 0)
        self.assertTrue(np.all(colours[:, 3] == 255))
        self.assertTrue(np.all(colours[:, :3] == [220, 40, 30]))
        self.assertGreater(report["source_sampled_vertices"], 0)

    def test_enclosed_dark_detail_excludes_boundary_shadow(self):
        image = np.zeros((256, 256, 4), dtype=np.uint8)
        image[20:236, 20:236] = [230, 230, 230, 255]
        image[100:120, 110:130, :3] = 10
        image[220:236, 150:210, :3] = 10
        protected, components = enclosed_dark_markings(image)
        self.assertEqual(components, 1)
        self.assertTrue(protected[110, 120])
        self.assertFalse(protected[230, 180])


if __name__ == "__main__":
    unittest.main()
