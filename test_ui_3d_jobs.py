"""三条 3D 路径的状态恢复、文件边界、互斥与 HTTP 页面接入。"""

import base64
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import pipeline_ui
from ui_3d_jobs import ThreeDController, _write


class ThreeDJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
