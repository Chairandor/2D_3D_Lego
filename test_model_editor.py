from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import model_edit_bridge
import model_editing
import pipeline_ui as ui
import proxe_integration as proxe
import stage_console_worker as worker


class FakeEditor:
    def status(self, workspace):
        return {"running": False, "configured": True, "message": "ready"}

    def start(self, workspace, payload, *, source, prepare_lego):
        self.prepare_lego = prepare_lego
        output = workspace / (payload["instruction"] + ".glb")
        output.write_bytes(source.read_bytes() + payload["instruction"].encode())
        proxe.write_state(workspace, {"latest_result": str(output)})


class ModelEditorTests(unittest.TestCase):
    def test_bridge_returns_edits_for_local_preview_and_save(self):
        import trimesh

        class RemoteEditor(FakeEditor):
            def start(self, workspace, payload, *, source, prepare_lego):
                self.prepare_lego = prepare_lego
                self.source = source
                output = workspace / "edited.glb"
                trimesh.creation.box(extents=[1, 2, 3]).export(output)
                proxe.write_state(workspace, {
                    "latest_result": str(output),
                    "latest_edit": {"preserve_existing_texture": False},
                })

        with tempfile.TemporaryDirectory(dir=ui.ROOT) as directory:
            parent = Path(directory).resolve()
            server_root = parent / "server"
            local_root = parent / "local"
            local_root.mkdir()
            original = local_root / "original.glb"
            trimesh.creation.box().export(original)
            remote_editor = RemoteEditor()
            server_sessions = model_editing.Sessions(server_root, remote_editor)
            secret = "model-edit-test-token-" + "1" * 32
            with ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler) as server, \
                    patch.object(ui, "MODEL_EDITS", server_sessions), \
                    patch.dict("os.environ", {"MODEL_EDIT_BRIDGE_TOKEN": secret, "MODEL_EDIT_BRIDGE_URL": ""}):
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}"
                    with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as conn:
                        conn.request("POST", "/api/model-edit/bridge-open",
                                     headers={"Authorization": "Bearer " + "x" * 32,
                                              "Content-Length": "0"})
                        with conn.getresponse() as denied:
                            self.assertEqual(denied.status, 403)
                    self.assertFalse(server_sessions.sessions)
                    client = model_edit_bridge.Client(url, secret)
                    local = model_editing.Sessions(local_root, FakeEditor(), client)
                    opened = local.open(local_root, original, "Original")
                    self.assertTrue(opened["configured"])
                    self.assertFalse(server_sessions.sessions)
                    token = opened["session"]
                    result = local.edit(token, "adjust")
                    self.assertTrue(result["can_save"])
                    self.assertTrue(result["can_check"])
                    self.assertFalse(remote_editor.prepare_lego)
                    self.assertEqual(remote_editor.source.read_bytes(), original.read_bytes())
                    self.assertNotEqual(local.preview(token).read_bytes(), original.read_bytes())
                    self.assertEqual(local.check(token)["checks"], ["网格闭合，无非流形边。"])
                    saved = local.save(token, "Edited")
                    saved_file, name = model_editing.saved_model(local_root, saved["id"])
                    self.assertEqual(name, "Edited")
                    self.assertEqual(saved_file.read_bytes(), local.preview(token).read_bytes())
                    remote_token = local.get(token).remote_session
                    self.assertFalse(server_sessions.status(remote_token)["can_save"])
                    with self.assertRaises(ValueError):
                        server_sessions.save(remote_token, "Unexpected")
                    local.close(token)
                    self.assertFalse(server_sessions.sessions)
                    self.assertTrue(saved_file.is_file())
                finally:
                    server.shutdown()
                    thread.join()

    def test_drafts_preview_and_save_before_optional_checks(self):
        import trimesh

        for preserve_color, invalid_output in ((True, False), (False, False), (False, True)):
            with self.subTest(preserve_color=preserve_color, invalid_output=invalid_output), \
                    tempfile.TemporaryDirectory(dir=ui.ROOT) as directory:
                run = Path(directory).resolve()
                original = run / "original.glb"
                box = trimesh.creation.box()
                box.export(original)
                controller = ui.AIEditController()
                sessions = model_editing.Sessions(run, controller)
                token = sessions.open(run, original, "Original")["session"]
                session = sessions.get(token)
                self.assertFalse(sessions.status(token)["can_check"])
                with self.assertRaises(ValueError):
                    sessions.check(token)
                controller.run_dir = session.workspace
                edit_folder = proxe.create_edit_folder(session.workspace)
                previous = session.workspace / "previous.glb"
                box.export(previous)
                (edit_folder / "edit_plan.json").write_text(json.dumps({
                    "category": "sample", "route": "proxe_full", "preserve_existing_texture": preserve_color,
                }), encoding="utf-8")
                output = edit_folder / "output.glb"
                if invalid_output:
                    output.write_bytes(b"broken GLB")
                else:
                    open_box = box.copy()
                    open_box.update_faces(range(len(open_box.faces) - 1))
                    open_box.export(output)

                command = ([], run)
                color_check = Mock(side_effect=ValueError("color drift"))
                with patch.object(proxe, "build_plan_command", return_value=command), \
                        patch.object(proxe, "build_command", return_value=command), \
                        patch.object(proxe, "build_preserve_appearance_command") as build_calibration, \
                        patch.object(controller, "_execute"), \
                        patch.object(proxe, "validate_mesh", wraps=proxe.validate_mesh) as mesh_check, \
                        patch.object(proxe, "validate_appearance_preservation", color_check):
                    controller._run(edit_folder, previous, "edit", "", 1, prepare_lego=False)
                    build_calibration.assert_not_called()
                    mesh_check.assert_not_called()
                    color_check.assert_not_called()
                    status = sessions.status(token)
                    self.assertEqual(status["can_save"], not invalid_output)
                    self.assertEqual(status["can_check"], not invalid_output)
                    if invalid_output:
                        self.assertEqual(status["message"], "编辑失败")
                        self.assertEqual(sessions.preview(token), session.source)
                    else:
                        raw = output.read_bytes()
                        self.assertEqual(sessions.preview(token), output)
                        saved = sessions.save(token, "Reviewed")
                        mesh_check.assert_not_called()
                        color_check.assert_not_called()
                        checks = sessions.check(token)
                        mesh_check.assert_called_once_with(output, require_watertight=False)
                        self.assertTrue(checks["checks"][0].startswith("网格有 3 条开口边"))
                        if preserve_color:
                            color_check.assert_called_once_with(previous, output)
                            self.assertTrue(checks["checks"][1].startswith("保色检查未通过"))
                        else:
                            color_check.assert_not_called()
                            self.assertEqual(len(checks["checks"]), 1)
                        self.assertEqual(output.read_bytes(), raw)
                        self.assertEqual(model_editing.saved_model(run, saved["id"])[0].read_bytes(), raw)
                sessions.close(token)

    def test_explicit_recovery_can_be_reopened_after_restart(self):
        with tempfile.TemporaryDirectory(dir=ui.ROOT) as directory:
            run = Path(directory).resolve()
            original = run / "original.glb"
            original.write_bytes(b"original")
            sessions = model_editing.Sessions(run, FakeEditor())
            token = sessions.open(run, original, "Original")["session"]
            sessions.edit(token, "result")
            workspace = sessions.get(token).workspace
            restarted = model_editing.Sessions(run, FakeEditor())
            with self.assertRaises(ValueError):
                restarted.status(token)
            (workspace / "recovery.json").write_text(json.dumps({"run": str(run), "name": "Recovered"}))
            self.assertTrue(restarted.status(token)["can_save"])
            self.assertEqual(restarted.preview(token).read_bytes(), b"originalresult")
            self.assertEqual(list(model_editing.saved_models(run)), [])
            restarted.close(token)
            self.assertFalse(workspace.exists())

    def test_only_named_results_survive_closing_the_editor(self):
        with tempfile.TemporaryDirectory(dir=ui.ROOT) as directory:
            run = Path(directory).resolve()
            original = run / "original.glb"
            original.write_bytes(b"original")
            editor = FakeEditor()
            sessions = model_editing.Sessions(run, editor)
            token = sessions.open(run, original, "Original")["session"]
            workspace = sessions.get(token).workspace
            sessions.edit(token, "first")
            self.assertFalse(editor.prepare_lego)
            self.assertEqual(list(model_editing.saved_models(run)), [])
            with self.assertRaises(ValueError):
                sessions.save(token, " ")
            self.assertTrue(sessions.status(token)["can_save"])
            saved = sessions.save(token, "My saved model")
            sessions.edit(token, "second")
            self.assertEqual(sessions.preview(token).read_bytes(), b"originalfirstsecond")
            sessions.close(token)
            self.assertFalse(workspace.exists())
            path, name = model_editing.saved_model(run, saved["id"])
            self.assertEqual((name, path.read_bytes()), ("My saved model", b"originalfirst"))
            self.assertEqual(original.read_bytes(), b"original")
            with self.assertRaises(ValueError):
                sessions.preview(token)

    def test_same_stud_results_have_separate_paths_and_verification_targets(self):
        with tempfile.TemporaryDirectory(dir=ui.ROOT) as directory:
            run = Path(directory).resolve()
            source = run / "unitex/textured_mesh.glb"
            source.parent.mkdir()
            source.write_bytes(b"original")
            sessions = model_editing.Sessions(run, FakeEditor())
            token = sessions.open(run, source, "Original")["session"]
            ids = []
            for name in ("Tall", "Wide"):
                sessions.edit(token, name)
                ids.append(sessions.save(token, name)["id"])
            sessions.close(token)

            def emit_preview(ply, folder, studs, bundle):
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "model.mpd").write_text(str(ply), encoding="utf-8")

            def convert(glb, ply, **_):
                ply.parent.mkdir(parents=True, exist_ok=True)
                ply.write_bytes(glb.read_bytes())

            controller = ui.PipelineController()
            controller.load_run(run)
            with patch.object(controller, "_launch") as launch, \
                    patch.object(worker.pipeline, "convert_via_lego_python", side_effect=convert), \
                    patch.object(worker.pipeline, "run_legolization_preview", side_effect=emit_preview), \
                    patch.object(worker, "read_preview", return_value={"token": "preview"}):
                for model_id in ids:
                    controller.start_stage({"action": "lego", "output_dir": str(run), "studs": 8, "model_id": model_id})
                    plan_path = Path(launch.call_args.args[0][-1])
                    self.assertEqual(json.loads(plan_path.read_text())["inputs"]["glb"],
                                     str(model_editing.saved_model(run, model_id)[0]))
                    worker.execute(plan_path)
                    with patch.object(ui, "pending_preview") as pending:
                        controller.verify_lego(run, 8, model_id)
                        pending.assert_called_once_with(run / "lego_sources" / model_id / "lego-8")
            cards = [item for item in ui.artifact_inventory(run) if item.get("studs") == 8]
            self.assertEqual({item["lego_model_id"] for item in cards}, set(ids))
            self.assertEqual(len({item["relative"] for item in cards}), 2)
            self.assertEqual(len({item["url"] for item in cards}), 2)
