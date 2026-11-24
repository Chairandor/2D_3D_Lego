import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import stage_console as console
import stage_console_worker as worker
import ui_result_folders
import ui_revisions


def put(root, relative, timestamp):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative.encode())
    os.utime(path, (timestamp, timestamp))
    return path


class StageConsoleTests(unittest.TestCase):
    def test_import_recovers_relocated_and_unindexed_png_versions(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            base = Path(temp)
            project = base / "project"
            wrapper = base / "example"
            source = wrapper / "example"
            previous = r"E:\old\example"
            foreground = source / console.FOREGROUND
            foreground.parent.mkdir(parents=True)
            Image.new("RGBA", (96, 96), "green").save(foreground)
            assets = source / "ui_revisions/assets"
            assets.mkdir(parents=True)
            for name in ("chosen.png", "older.png"):
                Image.new("RGBA", (48, 32), "yellow").save(assets / name)
            console.atomic_json(source / "pipeline_state.json", {"foreground": previous + r"\input\foreground.png"})
            console.atomic_json(source / "ui_revisions/state.json", {"pairs": {console.FOREGROUND: {
                "original": previous + r"\input\foreground.png",
                "candidate": previous + r"\ui_revisions\assets\chosen.png",
                "origin": "upload", "choice": "candidate",
            }}})
            (source / "input/.env").write_text("private", encoding="utf-8")
            resolved, kind = ui_result_folders.resolve_folder(wrapper)
            self.assertEqual((resolved, kind), (source, "image"))
            run = ui_result_folders.import_image_folder(resolved, project)
            self.assertEqual(ui_result_folders.import_image_folder(resolved, project), run)
            self.assertEqual(ui_result_folders.imported_from(run), str(source))
            self.assertEqual(console.Console(run, None, project).inputs("geometry")[console.FOREGROUND],
                             run / "ui_revisions/assets/chosen.png")
            self.assertEqual({path.name for path in console.history(run, project)[console.FOREGROUND]},
                             {"foreground.png", "chosen.png", "older.png"})
            self.assertFalse((run / "input/.env").exists())
            self.assertTrue((source / "input/.env").exists())

    def test_card_upload_replaces_active_version_without_an_assistant_choice(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            original = put(run, console.FOREGROUND, 100)
            older = Path(ui_revisions.add_candidate(run, console.FOREGROUND, "first.png", b"first", run)["candidate"])
            result = ui_revisions.add_candidate(run, console.FOREGROUND, "upload.png", b"upload", run, adopt=True)
            uploaded = Path(result["selected_path"])
            self.assertIsNone(ui_revisions.selected_pair(run, console.FOREGROUND, run))
            self.assertEqual(console.Console(run, None, run).inputs("geometry")[console.FOREGROUND], uploaded)
            self.assertEqual(set(console.history(run, run)[console.FOREGROUND]), {original, older, uploaded})

            ui_revisions.add_candidate(run, console.FOREGROUND, "assistant.png", b"edit", run, origin="assistant")
            pending = ui_revisions.selected_pair(run, console.FOREGROUND, run)
            self.assertIsNone(pending["choice"])
            self.assertEqual(Path(pending["original"]), uploaded)
            self.assertEqual(console.Console(run, None, run).inputs("geometry")[console.FOREGROUND], uploaded)

    def test_repeated_revisions_are_listed_and_loaded_as_geometry_input(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            original = put(run, console.FOREGROUND, 100)
            put(run, console.SHAPE, 200)
            first = Path(ui_revisions.add_candidate(run, console.FOREGROUND, "first.png", b"first", run)["candidate"])
            second = Path(ui_revisions.add_candidate(run, console.FOREGROUND, "second.png", b"second", run,
                                                    origin="assistant")["candidate"])
            branch = ui_revisions.create_generation_branch(run)
            generated = put(branch, console.FOREGROUND, 300)
            versions = console.history(branch, run)[console.FOREGROUND]
            self.assertEqual(set(versions), {original, first, second, generated})
            ui_revisions.remember_active_run(branch, run)
            ui_revisions.select_version(branch, console.FOREGROUND, first, versions[first]["owner"])
            reopened = ui_revisions.restore_active_run(run, run)
            workbench = console.Console(reopened, None, run)
            self.assertEqual(workbench.inputs("geometry"), {console.FOREGROUND: first})
            self.assertIn("views", workbench.buttons())
            self.assertEqual(first.read_bytes(), b"first")

    def test_historical_sheet_uses_its_original_inputs_and_cache(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            fg = put(run, console.FOREGROUND, 100)
            shape = put(run, console.SHAPE, 200)
            sheet = put(run, console.SHEET, 300)
            cache = run / ".stage-work" / "old-views"
            cache.mkdir(parents=True)
            console.save_outputs(run, run, {console.SHEET: {"cache_run": str(cache)}},
                                 {console.FOREGROUND: fg, console.SHAPE: shape})
            branch = ui_revisions.create_generation_branch(run)
            new_shape = put(branch, console.SHAPE, 400)
            ui_revisions.select_version(branch, console.SHEET, sheet, run)
            workbench = console.Console(branch, None, run)
            self.assertEqual(workbench.current(console.SHAPE).file, new_shape)
            inputs = workbench.inputs("texture")
            self.assertEqual(inputs, {console.FOREGROUND: fg, console.SHAPE: shape,
                                      console.SHEET: sheet, "cache_run": cache})
            model = put(branch, console.COLOR, 500)
            console.save_outputs(branch, run, {console.COLOR: {}}, inputs)
            self.assertEqual(console.Console(branch, None, run).inputs("lego"), {"glb": model})

    def test_historical_color_is_used_instead_of_current_lego_mesh(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            for i, name in enumerate((console.FOREGROUND, console.SHAPE, console.SHEET, console.COLOR, console.PLY), 1):
                put(run, name, i * 100)
            old_model = Path(ui_revisions.add_candidate(run, console.COLOR, "old.glb", b"old", run)["candidate"])
            ui_revisions.select_version(run, console.COLOR, old_model, run)
            self.assertEqual(console.Console(run, None, run).inputs("lego"), {"glb": old_model})
            self.assertTrue((run / console.PLY).is_file())

    def test_new_geometry_blocks_old_sheet_and_texture_but_preserves_files(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            fg = put(run, console.FOREGROUND, 100)
            shape = put(run, console.SHAPE, 200)
            six = put(run, console.SHEET, 300)
            model = put(run, console.COLOR, 400)
            console.save_outputs(run, run, {console.SHEET: {"cache_run": str(run)}},
                                 {console.FOREGROUND: fg, console.SHAPE: shape})
            current = console.Console(run, None, run)
            self.assertEqual(current.inputs("texture")[console.SHEET], six)
            self.assertEqual(current.inputs("lego"), {"glb": model})
            os.utime(shape, (500, 500))
            current = console.Console(run, None, run)
            self.assertNotIn("views", current.buttons())
            self.assertIn("texture", current.buttons())
            self.assertIn("lego", current.buttons())
            self.assertTrue(six.is_file() and model.is_file())

    def test_manual_colour_model_is_used_instead_of_old_input_mesh(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            for i, name in enumerate((console.FOREGROUND, console.SHAPE, console.SHEET, console.COLOR, console.PLY), 1):
                put(run, name, i * 100)
            candidate = ui_revisions.add_candidate(run, console.COLOR, "colour.glb", b"painted", run)
            ui_revisions.choose(run, console.COLOR, "candidate", run)
            self.assertEqual(console.Console(run, None, run).inputs("lego"),
                             {"glb": Path(candidate["candidate"])})
            self.assertTrue((run / console.PLY).is_file())

    def test_transparent_input_can_start_geometry_and_foreground_only_writes_foreground(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            image = run / "source.png"
            im = Image.new("RGBA", (80, 80))
            ImageDraw.Draw(im).rectangle((20, 20, 60, 55), fill=(227, 40, 109, 255))
            im.save(image)
            state = console.Console(run, image, worker.pipeline.ROOT)
            self.assertEqual(state.inputs("geometry"), {console.FOREGROUND: image})
            self.assertIn("views", state.buttons())
            plan = run / ".stage-work/demo/plan.json"
            plan.parent.mkdir(parents=True)
            console.atomic_json(plan, {"action": "foreground", "run": str(run), "inputs": {"source": str(image)},
                                       "input_versions": {"source": console.identity(image)}})
            worker.execute(plan)
            self.assertTrue((run / console.FOREGROUND).is_file())
            self.assertFalse((run / "hunyuan").exists())
            self.assertFalse((run / "unitex").exists())
            self.assertEqual(json.loads((run / "pipeline_state.json").read_text())["stage"], "foreground_review")

    def test_geometry_and_six_view_actions_have_independent_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory(dir=worker.pipeline.ROOT) as temp:
            run = Path(temp)
            foreground = put(run, console.FOREGROUND, 100)

            def make_shape(ref, out, **_):
                self.assertEqual(ref, foreground)
                return put(out, "mesh_shape_only.glb", 200)

            def clean(shape, ref, out):
                self.assertEqual(ref, foreground)
                self.assertEqual(shape, out / "mesh_shape_only.glb")
                return put(out, "mesh_shape_review_clean.glb", 201)

            def make_views(ref, shape, out, **settings):
                self.assertEqual(ref, foreground)
                self.assertEqual(shape, run / console.SHAPE)
                self.assertEqual(settings["phase"], "stage1")
                self.assertTrue(settings["force"])
                self.assertIsNone(settings["six_view_override"])
                put(out, "mv_rgb.png", 300)
                put(out, "mv_alpha.png", 300)

            def execute(action):
                plan = run / f".stage-work/{action}/plan.json"
                plan.parent.mkdir(parents=True)
                inputs = console.Console(run, None, worker.pipeline.ROOT).inputs(action)
                console.atomic_json(plan, {
                    "action": action, "run": str(run),
                    "inputs": {key: str(p) for key, p in inputs.items()},
                    "input_versions": {key: console.identity(p) for key, p in inputs.items()},
                })
                worker.execute(plan)

            with patch.object(worker.pipeline, "reconstruct_hunyuan", side_effect=make_shape), \
                 patch.object(worker.pipeline, "clean_and_review_shape", side_effect=clean), \
                 patch.object(worker.pipeline, "run_unitex_remote", side_effect=make_views) as unitex:
                execute("geometry")
                self.assertFalse((run / console.SHEET).exists())
                unitex.assert_not_called()
                execute("views")
                unitex.assert_called_once()
            self.assertTrue((run / console.SHAPE).is_file() and (run / console.SHEET).is_file())
            self.assertFalse((run / console.COLOR).exists())
