"""三条 3D 路径的状态恢复、文件边界、互斥与 HTTP 页面接入。"""

import base64
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np

import pipeline_ui
import ui_scene_explorer
from segmentation_lab.openmask3d_worker import mask_cosine_scores
from ui_3d_jobs import ThreeDController, _write


class ThreeDJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "datasets/others").mkdir(parents=True)
        for name in ["bunny.stl", "rhino.ply", "tiko_10000_points.ply"]:
            (self.root / "datasets/others" / name).write_bytes(b"fixture")
        self.controller = ThreeDController(self.root, self.root)

    def test_three_demos_and_upload_format_boundary(self):
        self.assertEqual(len(self.controller.bootstrap()["flows"]), 3)
        path = Path(self.controller.upload("cloud.ply", b"ply\n", "ply_color"))
        self.assertTrue(path.is_relative_to(self.root / "ui_uploads"))
        with self.assertRaises(ValueError):
            self.controller.upload("image.png", b"content", "stl")
        with self.assertRaises(ValueError):
            self.controller.load("stl", str(self.root.parent))

    def test_restart_restores_selected_flow_and_assets(self):
        run = self.controller.runs / "existing"
        run.mkdir(parents=True)
        model = run / "mesh.glb"
        model.write_bytes(b"fixture")
        _write(run / "pipeline3d.json", dict(flow="ply_plain", status="prepared", output=str(run),
               source="rhino.ply", started=1, ended=2, assets=[dict(id="mesh", path=str(model), kind="mesh")]))
        _write(self.controller.runs / "selection.json", {"ply_plain": str(run)})
        restored = ThreeDController(self.root, self.root)
        result = restored.status("ply_plain")
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(len(result["assets"]), 1)
        self.assertTrue(result["assets"][0]["url"].startswith("/file?"))
        self.assertEqual(restored.status("stl")["status"], "idle")

    def test_loading_history_replaces_previous_in_memory_job(self):
        run = self.controller.runs / "history"
        run.mkdir(parents=True)
        _write(run / "pipeline3d.json", dict(flow="stl", status="prepared", source="bunny.stl", assets=[]))
        self.controller.current = dict(flow="stl", output=str(self.controller.runs / "old"), status="failed")
        result = self.controller.load("stl", str(run))
        self.assertEqual(result["status"], "prepared")

    def test_curated_example_selects_flow_without_running_solver(self):
        run = self.controller.runs / "example_jackjack64"
        run.mkdir(parents=True)
        model = run / "lego.glb"
        model.write_bytes(b"model")
        _write(run / "pipeline3d.json", dict(flow="ply_color", status="partial", output=str(run),
               source=str(self.root / "datasets/others/tiko_10000_points.ply"),
               example=dict(id="jackjack64", label="JackJack · 64 studs"),
               assets=[dict(id="lego", path=str(model), kind="mesh")]))
        self.assertEqual(self.controller.bootstrap()["examples"][0]["id"], "jackjack64")
        self.assertEqual(self.controller.load_example("jackjack64")["output"], str(run))
        self.assertFalse(self.controller.is_running())
        with self.assertRaises(ValueError):
            self.controller.load_example("../jackjack64")

    def test_mask_query_sort_uses_original_instance_columns(self):
        folder = self.root / "ui_uploads/openmask3d/0123456789abcdef"
        _write(folder / "dataset.json", dict(id="scene", label="Scene", masks="masks.npz"))
        _write(self.root / "openmask_scenes.local.json", {
            "scene": dict(label="Scene", scene=str(folder / "scene.ply"), masks=str(folder / "masks.npz")),
        })
        candidates = [dict(id=2, points=300), dict(id=0, points=200), dict(id=1, points=100)]
        scores = mask_cosine_scores(np.array([[1, 0], [3, 4], [0, 0]]), np.array([3, 4]))
        with patch.object(self.controller, "openmask_instances", return_value=dict(mask_count=3, instances=candidates)) as inspect:
            with patch("ui_3d_jobs.ui_openmask.rank_instances", return_value=scores):
                result = self.controller.query_openmask_scene(folder.name, "a baby doll", point=9)
                self.assertEqual([entry["id"] for entry in result["instances"]], [1, 0])
                self.assertEqual(inspect.call_args.kwargs["point"], 9)
            with patch("ui_3d_jobs.ui_openmask.rank_instances", return_value=scores[:2]):
                with self.assertRaisesRegex(ValueError, "行数与掩码列数"):
                    self.controller.query_openmask_scene(folder.name, "a baby doll")

    def test_cancel_before_process_creation_is_persisted(self):
        with patch("ui_3d_jobs.threading.Thread"):
            result = self.controller.start(dict(flow="stl", prepare_only=True))
            with self.assertRaises(RuntimeError):
                self.controller.start(dict(flow="stl"))
            self.controller.stop()
        self.controller._run()
        self.assertFalse(self.controller.is_running())
        data = json.loads((Path(result["output"]) / "pipeline3d.json").read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "cancelled")

    def test_scene_job_keeps_file_pipeline_selection_separate(self):
        regular = self.controller.runs / "regular"
        _write(regular / "pipeline3d.json", dict(flow="ply_color", output=str(regular), status="prepared"))
        self.controller.load("ply_color", str(regular))
        source = self.root / "datasets/others/tiko_10000_points.ply"
        with patch("ui_3d_jobs.threading.Thread"):
            scene = self.controller.start(dict(flow="ply_color", workflow="scene", source=str(source)))
            self.assertEqual(Path(scene["output"]).parent.name, "scene")
            self.assertTrue(self.controller.status("ply_color", output=scene["output"])["running"])
            status = self.controller.status("ply_color")
            self.assertEqual(status["output"], str(regular))
            self.assertFalse(status["running"])
            self.assertTrue(status["busy"])
            self.assertEqual(status["active_job"], dict(output=scene["output"], flow="ply_color", workflow="scene"))
            bundle = Path(scene["output"]) / "solver/legolization"
            _write(bundle / "bundle.json", {"stages": {"model": {"status": "complete"}}})
            model = bundle / "model/model.mpd"
            model.parent.mkdir()
            model.write_text("0 test", encoding="utf-8")
            status = self.controller.status("ply_color", output=scene["output"])
            self.assertEqual(status["assets"][0]["path"], str(model))
            self.assertTrue(status["running"])
            self.assertIn("正在处理装配说明", status["message"])
            self.controller.stop(scene["output"])
        self.controller._run()
        self.assertEqual(self.controller.status("ply_color", output=scene["output"])["status"], "cancelled")

    def test_instance_import_remains_available_during_another_conversion(self):
        with patch("ui_3d_jobs.threading.Thread"):
            active = self.controller.start(dict(flow="stl"))
        with patch.object(self.controller, "_call_openmask", return_value={"scene_points": 12}):
            target = self.controller.upload_openmask("instance.ply", b"ply\n", "instance")
        self.assertEqual(Path(target["path"]).read_bytes(), b"ply\n")
        self.assertEqual(self.controller.status("ply_color")["active_job"]["output"], active["output"])
        with self.assertRaisesRegex(RuntimeError, "已有 3D 任务运行中"):
            self.controller.start(dict(flow="ply_color", workflow="scene", source=target["path"]))
        self.controller.stop(active["output"])
        self.controller._run()
        self.assertIsNone(self.controller.status("ply_color")["active_job"])
        with patch("ui_3d_jobs.threading.Thread"):
            self.controller.start(dict(flow="ply_color", workflow="scene", source=target["path"]))

    def test_failed_record_write_releases_job_slot(self):
        with patch("ui_3d_jobs.threading.Thread"):
            run = self.controller.start(dict(flow="stl"))
        self.controller.stop(run["output"])
        with patch("ui_3d_jobs._write", side_effect=PermissionError("fixture")):
            self.controller._run()
        status = self.controller.status("stl")
        self.assertFalse(status["busy"])
        self.assertEqual(status["status"], "failed")
        self.assertIn("保存 3D 任务记录失败", status["message"])

    def test_scene_explorer_retains_only_verified_instance_route(self):
        with patch.dict("os.environ", {"OPENMASK3D_VIEWER_URL": "http://127.0.0.1:18765/"}):
            url = ui_scene_explorer.upstream_url("/api/instances/45/download.ply", "scene=jackjack_dense")
            self.assertEqual(url, "http://127.0.0.1:18765/api/instances/45/download.ply?scene=jackjack_dense")
            for path, query in (("/../../etc/passwd", ""), ("/api/scene", "scene=../test"),
                                ("/api/scene", "scene=jackjack_dense&scene=redwood")):
                with self.assertRaises(ValueError):
                    ui_scene_explorer.upstream_url(path, query)

    def test_http_embeds_same_theme_and_routes_upload(self):
        server = pipeline_ui.ThreadingHTTPServer(("127.0.0.1", 0), pipeline_ui.Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(pipeline_ui, "THREE_D", self.controller):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            client = HTTPConnection("127.0.0.1", server.server_port)
            self.addCleanup(client.close)
            client.request("GET", "/")
            response = client.getresponse()
            page = response.read().decode()
            self.assertIn('id="threeWorkflow"', page)
            self.assertIn('--yellow:#f2c94c', page)
            self.assertIn('id="imageWorkflow"', page)
            self.assertLess(page.index('id="modeSceneButton"'), page.index('id="modeArButton"'))
            self.assertIn('id="sceneWorkflow"', page)
            client.request("GET", "/api/3d/bootstrap")
            response = client.getresponse()
            self.assertEqual(len(json.loads(response.read())["flows"]), 3)
            body = json.dumps(dict(flow="ply_plain", name="input.ply", data=base64.b64encode(b"ply\n").decode()))
            client.request("POST", "/api/3d/upload", body, {"Content-Type": "application/json"})
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(Path(json.loads(response.read())["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
