"""三个文件入口、颜色策略、纹理资产和后端交接的回归验证。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import open3d as o3d
from PIL import Image
import trimesh

from legolizer.processing3d.file_pipeline import run_pipeline
from legolizer.processing3d.flow_config import validate_options
from legolizer.processing3d.texturing import export_texture_bundle, transfer_point_colors


class FilePipelineTests(unittest.TestCase):
    def test_textured_obj_and_glb_are_real_roundtrip_assets(self):
        mesh = o3d.geometry.TriangleMesh.create_box(8, 16, 9.6)
        cloud = o3d.geometry.PointCloud(mesh.vertices)
        colors = np.zeros((len(cloud.points), 3))
        colors[:, 0] = np.asarray(cloud.points)[:, 0] / 8
        colors[:, 2] = 1 - colors[:, 0]
        cloud.colors = o3d.utility.Vector3dVector(colors)
        transfer_point_colors(mesh, cloud)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            metadata = export_texture_bundle(mesh, base)
            image = Image.open(base / metadata["texture"])
            self.assertEqual(list(image.size), metadata["atlas_size"])
            self.assertGreater(len(np.unique(np.asarray(image).reshape(-1, 3), axis=0)), 2)
            obj = trimesh.load(base / metadata["obj"], force="mesh", process=False)
            self.assertEqual(obj.visual.kind, "texture")
            self.assertIsNotNone(obj.visual.material.image)
            np.testing.assert_allclose(obj.extents, [8, 16, 9.6])
            scene = trimesh.load(base / metadata["glb"], force="scene", process=False)
            np.testing.assert_allclose(scene.extents, [8, 9.6, 16], atol=1e-5)
            self.assertTrue(all(g.visual.kind == "texture" for g in scene.geometry.values()))
            with zipfile.ZipFile(base / metadata["archive"]) as archive:
                self.assertEqual(set(archive.namelist()), {"mesh_textured.obj", "mesh_textured.mtl", "mesh_texture.png"})

    def test_single_color_ply_explicitly_discards_rgb(self):
        mesh = o3d.geometry.TriangleMesh.create_sphere(.8, 8)
        mesh.compute_vertex_normals()
        o3d.utility.random.seed(7)
        cloud = mesh.sample_points_uniformly(350)
        cloud.paint_uniform_color([1, 0, 0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "colored.ply"
            self.assertTrue(o3d.io.write_point_cloud(str(source), cloud))
            original = source.read_bytes()
            result = run_pipeline(source, root / "plain", "ply_plain", {"source_unit_mm": 1, "poisson_depth": 5}, prepare_only=True)
            self.assertEqual(result["status"], "prepared")
            self.assertTrue(result["report"]["source"]["source_has_colors"])
            self.assertFalse(result["report"]["source"]["has_colors"])
            reconstructed = o3d.io.read_triangle_mesh(str(root / "plain/mesh/reconstructed_mm.ply"))
            self.assertFalse(reconstructed.has_vertex_colors())
            self.assertEqual(source.read_bytes(), original)

    def test_stl_reaches_only_legolization_and_preserves_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mesh = o3d.geometry.TriangleMesh.create_box(32, 16, 9.6)
            mesh.compute_vertex_normals()
            source = root / "cube.stl"
            o3d.io.write_triangle_mesh(str(source), mesh)
            fake = dict(bundle_status="partial", exit_code=3, buildable=False, stable=False,
                        model_path=None, bundle_path=str(root / "output/bundle.json"),
                        instructions_status="skipped", instruction_step_count=None, instruction_audit_valid=None)
            with patch("legolizer.processing3d.file_pipeline.run_strict_backend", return_value=fake) as backend:
                result = run_pipeline(source, root / "output", "stl", {"source_unit_mm": 1, "source_up_axis": "z", "target_width_studs": 4})
            self.assertEqual(result["status"], "partial")
            self.assertEqual(backend.call_args.kwargs["support_mode"], "table")
            self.assertFalse(backend.call_args.kwargs["hollow"])
            self.assertTrue((root / "output/source_occupancy.npz").is_file())
            self.assertEqual(result["stages"][-1]["status"], "partial")

    def test_color_route_rejects_missing_rgb_and_keeps_error_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.random.default_rng(0).random((30, 3))))
            source = root / "plain.ply"
            o3d.io.write_point_cloud(str(source), cloud)
            with self.assertRaisesRegex(ValueError, "没有颜色"):
                run_pipeline(source, root / "out", "ply_color", {}, prepare_only=True)
            import json
            result = json.loads((root / "out/pipeline3d.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "failed")

    def test_invalid_parameters_are_rejected(self):
        for key, value in [("source_unit_mm", float("nan")), ("target_width_studs", True),
                           ("poisson_depth", 20), ("duration_seconds", -1), ("color", -1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_options("stl", {key: value})
        with self.assertRaisesRegex(ValueError, "点云"):
            validate_options("ply_color", {"source_up_axis": "auto"})


if __name__ == "__main__":
    unittest.main()
