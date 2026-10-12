"""Regression checks for the optional, repo-local refinement stage."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image
import trimesh
from trimesh.visual.color import ColorVisuals

from lego_refinement import (
    adaptive_plane_refine,
    flatten_shallow_markings,
    guided_normal_refine,
    multiscale_surface_fairing,
    planarize_broad_surfaces,
    recover_dark_image_details,
    remove_tiny_colour_islands,
    smooth_non_feature_surfaces,
)


class RefinementTests(unittest.TestCase):
    def test_planarization_reduces_noise_with_small_edge_movement(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
        for _ in range(3):
            mesh = mesh.subdivide()
        vertices = np.asarray(mesh.vertices).copy()
        front = np.isclose(vertices[:, 0], 0.5)
        interior = front & (np.abs(vertices[:, 1]) < 0.49) & (np.abs(vertices[:, 2]) < 0.49)
        boundary = front & ~interior
        vertices[interior, 0] += 0.008 * np.sin(np.arange(int(interior.sum())) * 2.0)
        mesh.vertices = vertices
        before = float(np.std(mesh.vertices[interior, 0]))

        report = planarize_broad_surfaces(mesh)

        self.assertGreater(report["moved_vertices"], 0)
        self.assertLess(float(np.std(mesh.vertices[interior, 0])), before)
        self.assertLess(
            float(np.max(np.linalg.norm(mesh.vertices[boundary] - vertices[boundary], axis=1))),
            0.005,
        )
        self.assertTrue(mesh.is_watertight)

    def test_colour_cleanup_protects_marked_detail(self) -> None:
        mesh = trimesh.creation.box()
        for _ in range(2):
            mesh = mesh.subdivide()
        colours = np.tile(np.array([244, 244, 244, 255], dtype=np.uint8), (len(mesh.vertices), 1))
        colours[4, :3] = [255, 109, 119]
        colours[15, :3] = [27, 42, 52]
        mesh.visual = ColorVisuals(vertex_colors=colours)
        protected = np.zeros(len(mesh.vertices), dtype=bool)
        protected[15] = True

        report = remove_tiny_colour_islands(mesh, protected)

        self.assertGreaterEqual(report["recoloured_vertices"], 1)
        np.testing.assert_array_equal(mesh.visual.vertex_colors[4, :3], [244, 244, 244])
        np.testing.assert_array_equal(mesh.visual.vertex_colors[15, :3], [27, 42, 52])

    def test_smoothing_reduces_ripples_without_moving_box_corners(self) -> None:
        mesh = trimesh.creation.box()
        for _ in range(3):
            mesh = mesh.subdivide()
        vertices = np.asarray(mesh.vertices).copy()
        face = np.isclose(vertices[:, 2], -0.5)
        interior = face & (np.abs(vertices[:, 0]) < 0.45) & (np.abs(vertices[:, 1]) < 0.45)
        corners = (np.abs(vertices) == 0.5).all(axis=1)
        vertices[interior, 2] += 0.007 * np.sin(np.arange(int(interior.sum())) * 2.0)
        mesh.vertices = vertices
        before = float(np.std(mesh.vertices[interior, 2]))

        report = smooth_non_feature_surfaces(mesh)

        self.assertGreater(report["moved_vertices"], 0)
        self.assertLess(float(np.std(mesh.vertices[interior, 2])), before)
        np.testing.assert_array_equal(mesh.vertices[corners], vertices[corners])

    def test_guided_normals_denoise_a_rotated_box(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 0.8, 0.6))
        for _ in range(3):
            mesh = mesh.subdivide()
        rotation = trimesh.transformations.euler_matrix(0.23, 0.41, -0.17)
        mesh.apply_transform(rotation)
        target = np.asarray(mesh.vertices).copy()
        rng = np.random.default_rng(7)
        mesh.vertices = target + rng.normal(0.0, 0.006, target.shape)
        before = float(np.mean(np.linalg.norm(mesh.vertices - target, axis=1)))

        report = guided_normal_refine(mesh)

        after = float(np.mean(np.linalg.norm(mesh.vertices - target, axis=1)))
        self.assertGreater(report["moved_vertices"], 0)
        self.assertLess(after, before)
        self.assertTrue(mesh.is_watertight)

    def test_guided_normals_preserve_clean_curved_shape(self) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
        initial_volume = float(mesh.volume)

        report = guided_normal_refine(mesh)

        self.assertLess(report["maximum_move"], 0.02)
        self.assertLess(abs(float(mesh.volume) / initial_volume - 1.0), 0.03)

    def test_adaptive_planes_improve_noisy_rotated_box(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 0.8, 0.6))
        for _ in range(3):
            mesh = mesh.subdivide()
        mesh.apply_transform(trimesh.transformations.euler_matrix(0.23, 0.41, -0.17))
        target = np.asarray(mesh.vertices).copy()
        rng = np.random.default_rng(11)
        mesh.vertices = target + rng.normal(0.0, 0.006, target.shape)
        before = float(np.mean(np.linalg.norm(mesh.vertices - target, axis=1)))

        report = adaptive_plane_refine(mesh)

        after = float(np.mean(np.linalg.norm(mesh.vertices - target, axis=1)))
        self.assertGreaterEqual(report["planes"], 3)
        self.assertLess(after, before)
        self.assertTrue(mesh.is_watertight)

    def test_adaptive_planes_leave_sphere_unclassified(self) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
        original = np.asarray(mesh.vertices).copy()

        report = adaptive_plane_refine(mesh)

        self.assertEqual(report["planes"], 0)
        np.testing.assert_array_equal(mesh.vertices, original)

    def test_adaptive_planes_cover_all_box_faces(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 0.8, 0.6))
        for _ in range(3):
            mesh = mesh.subdivide()

        report = adaptive_plane_refine(mesh)

        self.assertEqual(report["planes"], 6)
        self.assertLess(report["maximum_move"], 1e-6)

    def test_adaptive_planes_preserve_cylinder_wall(self) -> None:
        clean = trimesh.creation.cylinder(radius=0.4, height=1.0, sections=64)
        for _ in range(2):
            clean = clean.subdivide()
        rng = np.random.default_rng(5)
        for noise in (0.0, 0.0005, 0.001, 0.002):
            with self.subTest(noise=noise):
                mesh = clean.copy()
                mesh.vertices = mesh.vertices + rng.normal(0.0, noise, mesh.vertices.shape)
                before = mesh.vertices.copy()
                report = adaptive_plane_refine(mesh)
                if noise == 0.0:
                    self.assertEqual(report["planes"], 2)
                # The narrow cylindrical side must not be snapped into
                # spurious planes. Its vertices are >0.1 away from both caps.
                wall = np.abs(before[:, 2]) < 0.4
                wall_move = np.linalg.norm(mesh.vertices[wall] - before[wall], axis=1)
                self.assertLess(float(np.percentile(wall_move, 95)), 0.002)

    def test_multiscale_fairing_denoises_rotated_box(self) -> None:
        mesh = trimesh.creation.box(extents=(1.0, 0.8, 0.6))
        for _ in range(3):
            mesh = mesh.subdivide()
        mesh.apply_transform(trimesh.transformations.euler_matrix(0.23, 0.41, -0.17))
        target = mesh.vertices.copy()
        rng = np.random.default_rng(19)
        mesh.vertices = target + rng.normal(0.0, 0.006, target.shape)
        before = float(np.mean(np.linalg.norm(mesh.vertices - target, axis=1)))

        report = multiscale_surface_fairing(mesh, strength=8.0)

        after = float(np.mean(np.linalg.norm(mesh.vertices - target, axis=1)))
        self.assertGreater(report["moved_vertices"], 0)
        self.assertLess(after, before)
        self.assertTrue(mesh.is_watertight)

    def test_multiscale_fairing_preserves_clean_sphere(self) -> None:
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
        initial_volume = float(mesh.volume)

        report = multiscale_surface_fairing(mesh, strength=8.0)

        self.assertLess(report["maximum_move"], 0.01)
        self.assertLess(abs(float(mesh.volume) / initial_volume - 1.0), 0.03)

    def test_source_detail_recovers_front_marking(self) -> None:
        mesh = trimesh.creation.box(extents=(0.4, 0.4, 0.3))
        for _ in range(2):
            mesh = mesh.subdivide()
        mesh.visual = ColorVisuals(
            vertex_colors=np.tile([244, 244, 244, 255], (len(mesh.vertices), 1))
        )
        rgba = np.full((128, 128, 4), 255, dtype=np.uint8)
        rgba[53:75, 53:75, :3] = [27, 42, 52]
        with TemporaryDirectory() as temporary:
            image_path = Path(temporary) / "input.png"
            Image.fromarray(rgba, "RGBA").save(image_path)
            protected, report = recover_dark_image_details(mesh, image_path)

        self.assertEqual(report["dark_regions"], 1)
        self.assertGreater(int(protected.sum()), 0)
        self.assertTrue(np.all(mesh.visual.vertex_colors[protected, 0] == 27))

    def test_shallow_marking_is_flattened_on_planar_face(self) -> None:
        mesh = trimesh.creation.box()
        for _ in range(5):
            mesh = mesh.subdivide()
        vertices = np.asarray(mesh.vertices).copy()
        marked = (
            np.isclose(vertices[:, 2], -0.5)
            & (np.abs(vertices[:, 0]) < 0.05)
            & (np.abs(vertices[:, 1]) < 0.05)
        )
        vertices[marked, 2] += 0.01
        mesh.vertices = vertices

        report = flatten_shallow_markings(mesh, marked)

        self.assertGreater(report["flattened_vertices"], 0)
        self.assertLess(float(np.median(np.abs(mesh.vertices[marked, 2] + 0.5))), 0.005)


if __name__ == "__main__":
    unittest.main()
