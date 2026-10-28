from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pipeline_ui
import ui_revisions
import ui_run_state
from test_ui_six_view import write_six_view, sheet_bytes


class PipelineUITests(unittest.TestCase):
    def _completed_texture_revision(self, directory: Path) -> tuple[Path, Path, Path]:
        run_dir = directory / "main"
        source = directory / "source.png"
        source.write_bytes(b"original input")
        relatives = (
            "input/foreground.png", "hunyuan/mesh_shape_review_clean.glb",
            "unitex/mv_rgb.png", "unitex/textured_mesh.glb",
            "oriented/mesh_upright.glb", "oriented/lego_input.ply", "lego-8/model.mpd",
        )
        for relative in relatives:
            path = run_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original")
        (run_dir / "input/preprocess.json").write_text(
            json.dumps({"source": str(source)}), encoding="utf-8"
        )
        write_six_view(run_dir)
        candidate = ui_revisions.add_candidate(
            run_dir, "unitex/mv_rgb.png", "edited.png", sheet_bytes(), pipeline_ui.ROOT,
        )
        ui_revisions.choose(run_dir, "unitex/mv_rgb.png", "candidate", pipeline_ui.ROOT)
        branch = ui_revisions.create_texture_branch(
            run_dir, Path(candidate["candidate"]), run_dir / "input/foreground.png",
            run_dir / "hunyuan/mesh_shape_review_clean.glb", pipeline_ui.ROOT,
        )
        for relative in relatives[2:]:
            path = branch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(Path(candidate["candidate"]).read_bytes() if relative == "unitex/mv_rgb.png" else b"new result")
        pipeline_ui.artifact_groups(branch)
        for relative in ("unitex/textured_mesh.glb", "lego-8/model.mpd"):
            ui_revisions.choose(branch, relative, "candidate", pipeline_ui.ROOT)
        return run_dir, branch, source

    def test_reopening_legacy_run_restores_selected_texture_and_downstream(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir, branch, source = self._completed_texture_revision(Path(temporary))
            self.assertNotIn("active_run_dir", ui_revisions.load_state(run_dir))
            controller = pipeline_ui.PipelineController()
            controller.load_run(run_dir)
            self.assertEqual(controller.run_dir, branch)
            self.assertEqual(pipeline_ui.display_form_values(controller.run_dir, controller.image), (str(source), str(run_dir)))
            groups = {g["relative"]: g for g in pipeline_ui.artifact_groups(controller.run_dir)}
            for relative in ("unitex/textured_mesh.glb", "lego-8/model.mpd"):
                self.assertEqual(groups[relative]["choice"], "candidate")
                self.assertEqual(groups[relative]["candidate"]["size"], len(b"new result"))
            self.assertIsNone(groups["oriented/lego_input.ply"]["candidate"])
            self.assertEqual(groups["oriented/lego_input.ply"]["original"]["size"], len(b"new result"))
            self.assertEqual(ui_revisions.load_state(run_dir)["active_run_dir"], str(branch))
            # A second new controller must load exactly the same branch and
            # resume it when the form still contains the public input/folder.
            restarted = pipeline_ui.PipelineController()
            restarted.load_run(run_dir)
            _, _, actual_dir = pipeline_ui.build_command({
                "image_path": str(source), "output_dir": str(run_dir),
            }, active_run_dir=restarted.run_dir)
            self.assertEqual(actual_dir, branch)

    def test_switching_from_other_project_resumes_this_folders_saved_branch(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, image = self._completed_texture_revision(Path(temporary))
            ui_revisions.remember_active_run(branch, pipeline_ui.ROOT)
            other = Path(temporary) / "other"
            other.mkdir()
            for active in (None, other, public):
                command, actual_image, actual_run = pipeline_ui.build_command(
                    {"image_path": str(image), "output_dir": str(public)}, active_run_dir=active,
                )
                self.assertEqual(actual_run, branch)
                self.assertIn("--unitex-six-view", command)
                self.assertEqual(actual_image, public / "input/foreground.png")

    def test_legacy_self_pointer_does_not_override_exact_adopted_six_view(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            ui_revisions.update_state(public, lambda state: state.update(active_run_dir=str(public)))
            controller = pipeline_ui.PipelineController()
            controller.load_run(public)
            self.assertEqual(controller.run_dir, branch)
            self.assertEqual(ui_run_state.active_run(public, pipeline_ui.ROOT), branch)

    def test_manifest_reopen_does_not_scan_subdirectories_or_reset_choices(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            controller = pipeline_ui.PipelineController()
            controller.load_run(public)
            state_path = public / ui_run_state.STATE_FILE
            before = state_path.read_bytes()
            stray = branch / "lego-64/model.mpd"
            stray.parent.mkdir(); stray.write_bytes(b"unadopted")
            with mock.patch.object(pipeline_ui, "artifact_inventory", side_effect=AssertionError("no scan on reopen")):
                restarted = pipeline_ui.PipelineController()
                restarted.load_run(public)
                view = pipeline_ui.current_view(restarted.run_dir)
                groups = {group["relative"]: group for group in view["artifact_groups"]}
                self.assertEqual(groups["lego-8/model.mpd"]["choice"], "candidate")
                self.assertNotIn("lego-64/model.mpd", groups)
                self.assertEqual(restarted.run_dir, branch)
            self.assertEqual(state_path.read_bytes(), before)

    def test_missing_selected_artifact_reports_error_instead_of_using_original(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            controller = pipeline_ui.PipelineController()
            controller.load_run(public)
            (branch / "lego-8/model.mpd").unlink()
            with self.assertRaisesRegex(ValueError, "不会自动换成初版"):
                pipeline_ui.current_view(branch)

    def test_revision_change_updates_manifest_then_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            controller = pipeline_ui.PipelineController()
            controller.load_run(public)
            ui_revisions.choose(branch, "lego-8/model.mpd", "original", pipeline_ui.ROOT)
            groups = {g["relative"]: g for g in pipeline_ui.current_view(branch)["artifact_groups"]}
            self.assertEqual(groups["lego-8/model.mpd"]["choice"], "original")
            reopened = pipeline_ui.PipelineController()
            reopened.load_run(public)
            groups = {g["relative"]: g for g in pipeline_ui.current_view(reopened.run_dir)["artifact_groups"]}
            self.assertEqual(groups["lego-8/model.mpd"]["choice"], "original")

    def test_explicit_main_state_is_preserved_even_when_old_branch_files_exist(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            ui_revisions.remember_active_run(public, pipeline_ui.ROOT)
            pipeline_ui.current_view(public)
            self.assertEqual(ui_revisions.restore_active_run(public, pipeline_ui.ROOT), public)

    def test_manifest_missing_branch_or_corrupt_data_never_falls_back(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            ui_revisions.remember_active_run(branch, pipeline_ui.ROOT)
            state_file = public / ui_run_state.STATE_FILE
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["active_run"] = "ui_revisions/branches/missing"
            state_file.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不会自动退回初版"):
                ui_revisions.restore_active_run(public, pipeline_ui.ROOT)
            state_file.write_text("{corrupt", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不会自动退回初版"):
                ui_revisions.restore_active_run(public, pipeline_ui.ROOT)

    def test_pending_comparison_survives_reopen(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            controller = pipeline_ui.PipelineController()
            controller.load_run(public)
            ui_revisions.add_candidate(branch, "lego-8/model.mpd", "new.mpd", b"pending new", pipeline_ui.ROOT)
            pipeline_ui.current_view(branch)
            reopened = pipeline_ui.PipelineController()
            reopened.load_run(public)
            group = next(g for g in pipeline_ui.current_view(reopened.run_dir)["artifact_groups"] if g["relative"] == "lego-8/model.mpd")
            self.assertIsNone(group["choice"])
            self.assertIsNotNone(group["candidate"])

    def test_newly_generated_model_is_persisted_on_completion_refresh(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public, branch, _ = self._completed_texture_revision(Path(temporary))
            controller = pipeline_ui.PipelineController()
            controller.load_run(public)
            model = branch / "lego-16/model.mpd"
            model.parent.mkdir(); model.write_bytes(b"new completed model")
            pipeline_ui.current_view(branch, refresh=True)
            with mock.patch.object(pipeline_ui, "artifact_inventory", side_effect=AssertionError("no scan on reopen")):
                reopened = pipeline_ui.PipelineController()
                reopened.load_run(public)
                groups = pipeline_ui.current_view(reopened.run_dir)["artifact_groups"]
            self.assertIn("lego-16/model.mpd", [g["relative"] for g in groups])

    def test_concurrent_revision_and_active_pointer_writes_preserve_both(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            public = Path(temporary)
            def edit(index):
                ui_revisions.update_state(public, lambda state: state["pairs"].update({str(index): {"choice": "original"}}))
            def pointer(index):
                ui_revisions.update_state(public, lambda state: state.update(active_run_dir=str(public)))
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(edit, i) for i in range(30)] + [pool.submit(pointer, i) for i in range(30)]
                for future in futures:
                    future.result()
            state = ui_revisions.load_state(public)
            self.assertEqual(len(state["pairs"]), 30)
            self.assertEqual(state["active_run_dir"], str(public))

    def test_adopting_branch_persists_before_restart(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir, branch, source = self._completed_texture_revision(Path(temporary))
            controller = pipeline_ui.PipelineController()
            controller.activate_branch(branch, source)
            self.assertEqual(ui_revisions.restore_active_run(run_dir, pipeline_ui.ROOT), branch)
            with mock.patch.object(pipeline_ui, "DEFAULT_IMAGE", source), \
                 mock.patch.object(pipeline_ui, "default_run_dir", return_value=run_dir):
                restarted = pipeline_ui.PipelineController()
            self.assertEqual(restarted.run_dir, branch)

    def test_legacy_restore_does_not_guess_between_matching_branches(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir, branch, _ = self._completed_texture_revision(Path(temporary))
            settings = ui_revisions.branch_settings(branch, pipeline_ui.ROOT)
            ui_revisions.create_texture_branch(
                run_dir, Path(settings["six_view"]), Path(settings["source_image"]),
                Path(settings["from_shape"]), pipeline_ui.ROOT,
            )
            self.assertEqual(ui_revisions.restore_active_run(run_dir, pipeline_ui.ROOT), run_dir)
            # Once adopted explicitly, the persisted choice disambiguates it.
            ui_revisions.remember_active_run(branch, pipeline_ui.ROOT)
            self.assertEqual(ui_revisions.restore_active_run(run_dir, pipeline_ui.ROOT), branch)

    def test_restore_rejects_an_active_branch_from_another_project(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir, branch, _ = self._completed_texture_revision(Path(temporary))
            other = Path(temporary) / "other"
            other.mkdir()
            state = ui_revisions.load_state(run_dir)
            state["active_run_dir"] = str(other)
            ui_revisions._save_state(run_dir, state)
            self.assertEqual(ui_revisions.restore_active_run(run_dir, pipeline_ui.ROOT), branch)

    def test_revision_state_saves_do_not_share_a_temp_filename(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: ui_revisions._save_state(run_dir, {"version": 1, "pairs": {}}), range(40)))
            self.assertEqual(ui_revisions.load_state(run_dir)["pairs"], {})

    @unittest.skipUnless(os.name == "nt", "Windows file replacement semantics")
    def test_revision_state_retries_a_transient_windows_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            original_replace = Path.replace
            attempts = 0

            def flaky_replace(source: Path, target: Path) -> Path:
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    raise PermissionError(5, "Access denied", str(target))
                return original_replace(source, target)

            with mock.patch.object(ui_revisions.os, "name", "nt"), \
                 mock.patch.object(Path, "replace", flaky_replace):
                ui_revisions._save_state(run_dir, {"version": 1, "pairs": {}})
            self.assertEqual(attempts, 3)
            self.assertEqual(ui_revisions.load_state(run_dir)["pairs"], {})

    def test_new_candidate_after_reselecting_original_is_pending(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            original = run_dir / "input" / "foreground.png"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"original")
            first = ui_revisions.add_candidate(
                run_dir, "input/foreground.png", "first.png", b"first", pipeline_ui.ROOT
            )
            ui_revisions.choose(run_dir, "input/foreground.png", "original", pipeline_ui.ROOT)
            second = ui_revisions.add_candidate(
                run_dir, "input/foreground.png", "second.png", b"second", pipeline_ui.ROOT
            )
            selected = ui_revisions.selected_pair(
                run_dir, "input/foreground.png", pipeline_ui.ROOT
            )
            self.assertIsNotNone(selected)
            self.assertIsNone(selected["choice"])
            self.assertEqual(selected["candidate"], second["candidate"])
            self.assertEqual(Path(str(first["candidate"])).read_bytes(), b"first")
            self.assertEqual(original.read_bytes(), b"original")

    def test_build_command_preserves_review_approvals(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            root = Path(temporary)
            image = root / "input.png"
            image.write_bytes(b"png")
            run_dir = root / "run"
            command, selected, output = pipeline_ui.build_command(
                {
                    "image_path": str(image),
                    "output_dir": str(run_dir),
                    "accept_preprocess": True,
                    "accept_geometry": True,
                    "accept_unitex": False,
                    "build_lego": False,
                    "projection": "ltm",
                    "unitex_gpu": 1,
                    "target_studs": 24,
                }
            )
            self.assertEqual(selected, image.resolve())
            self.assertEqual(output, run_dir.resolve())
            self.assertIn("--accept-preprocess", command)
            self.assertIn("--accept-geometry", command)
            self.assertNotIn("--accept-unitex", command)
            self.assertNotIn("--build-lego", command)
            self.assertEqual(command[command.index("--unitex-gpu") + 1], "1")

    def test_artifact_inventory_only_lists_existing_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            target = run_dir / "unitex" / "mv_rgb.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"preview")
            artifacts = pipeline_ui.artifact_inventory(run_dir)
            self.assertEqual([item["relative"] for item in artifacts], ["unitex/mv_rgb.png"])

    def test_model_previews_include_lego_when_parts_library_exists(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            library = run_dir / "ldraw"
            (library / "parts").mkdir(parents=True)
            (library / "LDConfig.ldr").write_text("0 colours", encoding="utf-8")
            for relative in (
                "hunyuan/mesh_shape_review_clean.glb",
                "unitex/textured_mesh.glb",
                "oriented/mesh_upright.glb",
                "oriented/lego_input.ply",
                "lego-16/model.mpd",
            ):
                target = run_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"test")
            with mock.patch.object(pipeline_ui, "LDRAW_ROOT", library):
                artifacts = pipeline_ui.artifact_inventory(run_dir)
            previews = {
                item["relative"]: item.get("preview_format")
                for item in artifacts
            }
            self.assertEqual(previews["hunyuan/mesh_shape_review_clean.glb"], "glb")
            self.assertEqual(previews["unitex/textured_mesh.glb"], "glb")
            self.assertEqual(previews["oriented/mesh_upright.glb"], "glb")
            self.assertEqual(previews["oriented/lego_input.ply"], "ply")
            self.assertEqual(previews["lego-16/model.mpd"], "mpd")

    def test_lego_preview_falls_back_without_parts_library(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            model = run_dir / "lego-8" / "model.mpd"
            model.parent.mkdir()
            model.write_text("0 FILE model.mpd", encoding="utf-8")
            with mock.patch.object(pipeline_ui, "LDRAW_ROOT", run_dir / "missing-ldraw"):
                artifacts = pipeline_ui.artifact_inventory(run_dir)
            self.assertEqual(len(artifacts), 1)
            self.assertNotIn("preview_format", artifacts[0])

    def test_revision_pair_requires_confirmation_and_keeps_original(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            original = run_dir / "input" / "foreground.png"
            original.parent.mkdir()
            original.write_bytes(b"original")
            ui_revisions.add_candidate(run_dir, "input/foreground.png", "fixed.png", b"fixed", pipeline_ui.ROOT)
            groups = pipeline_ui.artifact_groups(run_dir)
            self.assertEqual(groups[0]["choice"], None)
            self.assertIsNotNone(groups[0]["candidate"])
            ui_revisions.choose(run_dir, "input/foreground.png", "candidate", pipeline_ui.ROOT)
            self.assertEqual(pipeline_ui.artifact_groups(run_dir)[0]["choice"], "candidate")
            self.assertEqual(original.read_bytes(), b"original")

    def test_branch_keeps_old_downstream_until_regenerated(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            for relative in ("input/foreground.png", "hunyuan/mesh_shape_review_clean.glb"):
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"old")
            image = run_dir / "input" / "fixed.png"
            image.write_bytes(b"new")
            branch = ui_revisions.create_input_branch(run_dir, image, pipeline_ui.ROOT)
            old = {g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}
            self.assertTrue(old["hunyuan/mesh_shape_review_clean.glb"]["stale"])
            new_model = branch / "hunyuan" / "mesh_shape_review_clean.glb"
            new_model.parent.mkdir(parents=True)
            new_model.write_bytes(b"new")
            pair = {g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}["hunyuan/mesh_shape_review_clean.glb"]
            self.assertIsNotNone(pair["candidate"])
            self.assertIsNone(pair["choice"])
            ui_revisions.choose(branch, "hunyuan/mesh_shape_review_clean.glb", "original", pipeline_ui.ROOT)
            self.assertEqual({g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}["hunyuan/mesh_shape_review_clean.glb"]["choice"], "original")

    def test_texture_candidate_branch_resumes_without_rebuilding_geometry(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary)
            paths = {}
            for relative in (
                "input/foreground.png", "hunyuan/mesh_shape_review_clean.glb",
                "unitex/mv_rgb.png", "unitex/textured_mesh.glb",
            ):
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
                paths[relative] = path
            write_six_view(run_dir)
            result = ui_revisions.add_candidate(
                run_dir, "unitex/mv_rgb.png", "fixed.png", sheet_bytes(),
                pipeline_ui.ROOT, origin="assistant",
            )
            branch = ui_revisions.create_texture_branch(
                run_dir, Path(str(result["candidate"])), paths["input/foreground.png"],
                paths["hunyuan/mesh_shape_review_clean.glb"], pipeline_ui.ROOT,
            )
            ui_revisions.register_generated_pair(
                branch, "unitex/mv_rgb.png", paths["unitex/mv_rgb.png"],
                Path(str(result["candidate"])), pipeline_ui.ROOT,
            )
            command, _, output = pipeline_ui.build_command({
                "image_path": str(paths["input/foreground.png"]), "output_dir": str(branch),
            })
            self.assertEqual(output, branch)
            self.assertEqual(command[command.index("--from-shape") + 1], str(paths["hunyuan/mesh_shape_review_clean.glb"]))
            self.assertEqual(command[command.index("--unitex-six-view") + 1], str(result["candidate"]))
            self.assertNotIn("--build-lego", command)
            stale = {g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}
            self.assertFalse(stale["input/foreground.png"]["stale"])
            self.assertFalse(stale["hunyuan/mesh_shape_review_clean.glb"]["stale"])
            self.assertFalse(stale["unitex/mv_rgb.png"]["stale"])
            self.assertTrue(stale["unitex/textured_mesh.glb"]["stale"])
            ui_revisions.choose(branch, "unitex/mv_rgb.png", "candidate", pipeline_ui.ROOT)
            copied_sheet = branch / "unitex" / "mv_rgb.png"
            copied_sheet.parent.mkdir(parents=True)
            copied_sheet.write_bytes(Path(result["candidate"]).read_bytes())
            after_run = {g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}
            self.assertIsNone(after_run["unitex/mv_rgb.png"]["candidate"])
            self.assertEqual(after_run["unitex/mv_rgb.png"]["original"]["size"], copied_sheet.stat().st_size)
            ui_revisions.add_candidate(
                branch, "unitex/mv_rgb.png", "another.png", sheet_bytes((180, 50, 40)), pipeline_ui.ROOT
            )
            another_edit = {g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}
            self.assertIsNotNone(another_edit["unitex/mv_rgb.png"]["candidate"])
            nested = ui_revisions.create_texture_branch(
                branch,
                Path(str(ui_revisions.selected_pair(
                    branch, "unitex/mv_rgb.png", pipeline_ui.ROOT
                )["candidate"])),
                ui_revisions.available_artifact(branch, "input/foreground.png", pipeline_ui.ROOT),
                ui_revisions.available_artifact(
                    branch, "hunyuan/mesh_shape_review_clean.glb", pipeline_ui.ROOT
                ),
                pipeline_ui.ROOT,
            )
            inherited = {g["relative"]: g for g in pipeline_ui.artifact_groups(nested)}
            self.assertIn("input/foreground.png", inherited)
            self.assertIn("hunyuan/mesh_shape_review_clean.glb", inherited)

    def test_unitex_branch_uses_new_derived_intermediates_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary) / "main"
            for relative in (
                "input/foreground.png", "hunyuan/mesh_shape_review_clean.glb",
                "unitex/mv_rgb.png", "unitex/textured_mesh.glb",
                "oriented/mesh_upright.glb", "oriented/lego_input.ply",
            ):
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"old")
            sheet = run_dir / "new_sheet.png"
            sheet.write_bytes(b"new sheet")
            branch = ui_revisions.create_texture_branch(
                run_dir, sheet, run_dir / "input/foreground.png",
                run_dir / "hunyuan/mesh_shape_review_clean.glb", pipeline_ui.ROOT,
            )
            for relative in (
                "unitex/textured_mesh.glb", "oriented/mesh_upright.glb",
                "oriented/lego_input.ply",
            ):
                path = branch / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"new result")

            groups = {group["relative"]: group for group in pipeline_ui.artifact_groups(branch)}
            self.assertIsNotNone(groups["unitex/textured_mesh.glb"]["candidate"])
            for relative in ("oriented/mesh_upright.glb", "oriented/lego_input.ply"):
                self.assertIsNone(groups[relative]["candidate"])
                self.assertEqual(groups[relative]["original"]["size"], len(b"new result"))
                self.assertIsNone(ui_revisions.selected_pair(branch, relative, pipeline_ui.ROOT))

            # Previously registered generated pairs must also disappear from
            # the UI, while an intentional upload remains selectable.
            relative = "oriented/mesh_upright.glb"
            ui_revisions.register_generated_pair(
                branch, relative, run_dir / relative, branch / relative, pipeline_ui.ROOT,
            )
            self.assertIsNone({g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}[relative]["candidate"])
            ui_revisions.add_candidate(
                branch, relative, "manual.glb", b"manual", pipeline_ui.ROOT,
            )
            self.assertIsNotNone({g["relative"]: g for g in pipeline_ui.artifact_groups(branch)}[relative]["candidate"])

    def test_visible_original_fields_resume_texture_branch_internally(self) -> None:
        with tempfile.TemporaryDirectory(dir=pipeline_ui.ROOT) as temporary:
            run_dir = Path(temporary) / "main"
            source = Path(temporary) / "source.png"
            source.write_bytes(b"original image")
            for relative in ("input/foreground.png", "hunyuan/mesh_shape_review_clean.glb", "unitex/mv_rgb.png"):
                path = run_dir / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
            (run_dir / "input" / "preprocess.json").write_text(
                json.dumps({"source": str(source)}), encoding="utf-8"
            )
            sheet = run_dir / "candidate.png"
            sheet.write_bytes(b"candidate")
            branch = ui_revisions.create_texture_branch(
                run_dir, sheet, run_dir / "input" / "foreground.png",
                run_dir / "hunyuan" / "mesh_shape_review_clean.glb", pipeline_ui.ROOT,
            )
            visible_image, visible_dir = pipeline_ui.display_form_values(branch, sheet)
            self.assertEqual(visible_image, str(source))
            self.assertEqual(visible_dir, str(run_dir))
            command, actual_image, actual_dir = pipeline_ui.build_command(
                {"image_path": visible_image, "output_dir": visible_dir}, active_run_dir=branch
            )
            self.assertEqual(actual_image, run_dir / "input" / "foreground.png")
            self.assertEqual(actual_dir, branch)
            self.assertIn("--unitex-six-view", command)
            self.assertNotIn("--build-lego", command)
            _, _, new_dir = pipeline_ui.build_command(
                {"image_path": visible_image, "output_dir": str(Path(temporary) / "other")},
                active_run_dir=branch,
            )
            self.assertNotEqual(new_dir, branch)

    def test_stale_badge_is_short_and_floats_on_card(self) -> None:
        self.assertIn('.artifact>.stale-tag{position:absolute;top:10px;right:10px', pipeline_ui.HTML)
        self.assertIn('font-size:11px;line-height:normal', pipeline_ui.HTML)
        self.assertIn("${g.stale?'<span class=\"stale-tag\">旧结果</span>':''}", pipeline_ui.HTML)
        self.assertNotIn("旧结果 · 尚未重新生成", pipeline_ui.HTML)
        self.assertNotIn("if(d.image_path){$('imagePath').value=d.image_path", pipeline_ui.HTML)

    def test_assistant_menu_is_single_grouped_picker(self) -> None:
        self.assertEqual(pipeline_ui.HTML.count('id="modelPicker"'), 1)
        self.assertIn("按阶段 · 自动选择对话模型", pipeline_ui.HTML)
        self.assertIn("直接选对话模型 · 不指定阶段", pipeline_ui.HTML)
        self.assertIn("assistantSelection={mode:button.dataset.mode,value:button.dataset.value}", pipeline_ui.HTML)

    def test_card_confirmation_is_an_overlay_not_an_extra_row(self) -> None:
        self.assertIn(".artifact.choice-card{position:relative}", pipeline_ui.HTML)
        self.assertIn(".choice-confirm{position:absolute;inset:0", pipeline_ui.HTML)
        self.assertIn("backdrop-filter:blur(6px)", pipeline_ui.HTML)
        self.assertIn('<div class="choice-prompt">确认选这版吗？</div>', pipeline_ui.HTML)

    def test_comparison_tags_only_appear_on_unselected_cards(self) -> None:
        self.assertIn('.artifact.choice-card>.version-tag{position:absolute;top:10px;right:10px', pipeline_ui.HTML)
        self.assertIn('${selectable?`<span class="version-tag">', pipeline_ui.HTML)
        self.assertNotIn('${version?`<span class="version-tag">', pipeline_ui.HTML)


if __name__ == "__main__":
    unittest.main()
