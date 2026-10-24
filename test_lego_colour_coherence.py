"""The palette cleanup should fix near-uniform regions, not real gradients."""

from __future__ import annotations

import unittest
import sys
import types
from unittest import mock

import numpy as np
import trimesh
from trimesh.visual.color import ColorVisuals

from lego_colour_coherence import stabilize_palette_regions


def coloured_box(source_rgb: np.ndarray, mapped_rgb: np.ndarray) -> trimesh.Trimesh:
    mesh = trimesh.creation.box()
    for _ in range(3):
        mesh = mesh.subdivide()
    assert source_rgb.shape == (len(mesh.vertices), 3)
    rgba = np.column_stack((mapped_rgb, np.full(len(mesh.vertices), 255, dtype=np.uint8)))
    mesh.visual = ColorVisuals(vertex_colors=rgba)
    return mesh


class ColourCoherenceTests(unittest.TestCase):
    def setUp(self) -> None:
        # Review CI does not fetch the large upstream checkout. Test the region
        # logic with a deterministic colour-distance adapter instead.
        package = types.ModuleType("legolization")
        colour = types.ModuleType("legolization.color")
        colour._srgb_to_oklab = lambda rgb: np.asarray(rgb, dtype=float) / 400.0
        patcher = mock.patch.dict(sys.modules, {"legolization": package, "legolization.color": colour})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_merges_near_uniform_area_split_across_palette_boundary(self) -> None:
        template = trimesh.creation.box()
        for _ in range(3):
            template = template.subdivide()
        right = template.vertices[:, 0] > -0.1
        source = np.empty((len(template.vertices), 3), dtype=np.uint8)
        source[~right] = (204, 150, 26)
        source[right] = (206, 153, 31)
        mapped = np.empty_like(source)
        mapped[~right] = (214, 121, 35)
        mapped[right] = (252, 172, 0)
        mesh = coloured_box(source, mapped)

        report = stabilize_palette_regions(mesh, source)

        self.assertGreater(report["recoloured_vertices"], 0)
        self.assertEqual(report["merged_regions"], 1)
        self.assertEqual(len(np.unique(mesh.visual.vertex_colors[:, :3], axis=0)), 1)

    def test_preserves_broad_smooth_gradient(self) -> None:
        template = trimesh.creation.box()
        for _ in range(3):
            template = template.subdivide()
        x = (template.vertices[:, 0] + 0.5)
        source = np.column_stack((np.full(len(x), 252), 130 + 60 * x, np.zeros(len(x)))).astype(np.uint8)
        mapped = np.where(
            (x < 0.5)[:, None],
            np.array([214, 121, 35], dtype=np.uint8),
            np.array([252, 172, 0], dtype=np.uint8),
        )
        mesh = coloured_box(source, mapped)

        report = stabilize_palette_regions(mesh, source)

        self.assertEqual(report["recoloured_vertices"], 0)
        np.testing.assert_array_equal(mesh.visual.vertex_colors[:, :3], mapped)

    def test_preserves_sharp_colour_boundary(self) -> None:
        template = trimesh.creation.box()
        for _ in range(3):
            template = template.subdivide()
        right = template.vertices[:, 0] > -0.1
        source = np.empty((len(template.vertices), 3), dtype=np.uint8)
        source[~right] = (255, 0, 0)
        source[right] = (0, 0, 0)
        mapped = source.copy()
        mesh = coloured_box(source, mapped)

        report = stabilize_palette_regions(mesh, source)

        self.assertEqual(report["recoloured_vertices"], 0)
        np.testing.assert_array_equal(mesh.visual.vertex_colors[:, :3], mapped)


if __name__ == "__main__":
    unittest.main()
