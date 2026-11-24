import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pipeline_ui
import ui_result_folders
import ui_revisions


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


class ResultFolderTests(unittest.TestCase):
    def test_open_external_folder_keeps_its_saved_choice_and_original_files(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            project, output = base / "current-ui", base / "copied project" / "成果"
            project.mkdir()
            branch = output / "ui_revisions/branches/adopted"
            first = output / "lego-8/model.mpd"
            chosen = branch / "lego-8/model.mpd"
            write(first, "old")
            write(chosen, "new")
            write(branch / "ui_branch.json", {"parent_run_dir": "/old/location", "stage": "texture"})
            write(output / "ui_current_state.json", {
                "version": 1, "active_run": branch.relative_to(output).as_posix(),
                "pipeline_state": {"stage": "complete"}, "artifact_groups": [{
                    "relative": "lego-8/model.mpd", "stage": "lego", "choice": "candidate", "stale": False,
                    "original": {"file": first.relative_to(output).as_posix()},
                    "candidate": {"file": chosen.relative_to(output).as_posix()},
                }],
            })
            write(output / "input" / "private.key", "private")
            before = sorted(str(p.relative_to(output)) for p in output.rglob("*"))
            with patch.object(pipeline_ui, "ROOT", project), patch.object(ui_revisions, "_PROJECT_ROOT", project):
                controller = pipeline_ui.PipelineController()
                with patch.object(pipeline_ui, "CONTROLLER", controller):
                    controller.load_run(ui_result_folders.parse_folder(f'"{output}"'))
                    status = controller.status()
                    self.assertEqual(status["display_output_dir"], str(output))
                    self.assertEqual(status["display_image"], "")
                    self.assertFalse(status["read_only_run"])
                    self.assertTrue(controller.run_dir.is_relative_to(project / "runs/imported"))
                    imported_model = controller.run_dir / "lego-8/model.mpd"
                    self.assertEqual(status["artifact_groups"][0]["choice"], "candidate")
                    self.assertEqual(pipeline_ui.lego_model_path(str(imported_model)), imported_model)
                    self.assertEqual(imported_model.read_text(encoding="utf-8"), "new")
                    can_download = getattr(pipeline_ui, "downloadable_file", None) or pipeline_ui.can_download
                    self.assertTrue(can_download(imported_model))
                    self.assertFalse(can_download(output / "input" / "private.key"))
                    self.assertEqual(sorted(str(p.relative_to(output)) for p in output.rglob("*")), before)

    def test_open_moved_legacy_folder_rebases_saved_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            project, folder = base / "ui", base / "renamed"
            project.mkdir()
            previous = "D:/old/output"
            branch = "ui_revisions/branches/approved"
            model = "lego-12/model.mpd"
            write(folder / "pipeline_state.json", {"foreground": previous + "/input/foreground.png"})
            write(folder / model, "original")
            write(folder / branch / model, "chosen")
            write(folder / branch / "ui_branch.json", {"parent_run_dir": previous, "stage": "texture"})
            write(folder / "ui_revisions/state.json", {"pairs": {}, "active_run_dir": f"{previous}/{branch}"})
            write(folder / branch / "ui_revisions/state.json", {"pairs": {
                model: {"original": f"{previous}/{model}", "candidate": f"{previous}/{branch}/{model}", "choice": "candidate"}
            }})
            with patch.object(pipeline_ui, "ROOT", project), patch.object(ui_revisions, "_PROJECT_ROOT", project):
                controller = pipeline_ui.PipelineController()
                controller.load_run(folder)
                self.assertTrue(controller.run_dir.is_relative_to(project / "runs/imported"))
                self.assertEqual(controller.run_dir.name, "approved")
                self.assertEqual(controller.status()["artifact_groups"][0]["choice"], "candidate")
                self.assertFalse((folder / "ui_current_state.json").exists())

    def test_open_moved_native_folder_rebases_windows_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            project, folder = base / "ui", base / "moved"
            project.mkdir()
            old_path = r"C:\original\run"
            original = "source/original.stl"
            finished = "solver/legolization/model/model.mpd"
            for relative in (original, finished, "voxel_preview.json"):
                write(folder / relative, "artifact")
            write(folder / "pipeline3d.json", {"flow": "stl", "status": "partial", "assets": [
                {"id": "original", "path": old_path + r"\source\original.stl"},
                {"id": "lego", "path": old_path + r"\solver\legolization\model\model.mpd"},
                {"id": "voxel", "path": old_path + r"\voxel_preview.json"},
                {"id": "outside", "path": str(project / "private.png")},
            ]})
            write(project / "private.png", "outside file")
            with patch.object(pipeline_ui, "ROOT", project):
                controller = pipeline_ui.PipelineController()
                controller.load_run(folder)
                status = controller.status()
                self.assertEqual(status["pipeline_state"]["flow"], "stl")
                self.assertEqual({item["relative"] for item in status["artifacts"]},
                                 {original, finished, "voxel_preview.json"})
                with self.assertRaisesRegex(ValueError, "未识别"):
                    controller.load_run(project)
                self.assertEqual(controller.run_dir, folder)
