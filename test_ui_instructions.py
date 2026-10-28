from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import ui_instructions as instructions


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def fixture(root: Path, source: Path, *, attachment: bool = False) -> Path:
    sha = instructions.digest(source)
    run = root / "runs/lego-instructions" / f"instructions_first_{sha[:12]}_fixture"
    book = run / "booklet"
    (book / "steps").mkdir(parents=True, exist_ok=True)
    (book / "parts").mkdir(exist_ok=True)
    (book / "instructions_cn.pdf").write_bytes(b"%PDF-1.4 fixture")
    (book / "steps/step_0001.png").write_bytes(b"fixture image")
    part = {"part_key": "plate_1x2", "ldraw_part": "3023", "colour_code": 15,
            "colour_name": "White", "quantity": 2}
    (book / "parts/3023_15.png").write_bytes(b"fixture thumbnail")
    steps = [{"index": 1, "attaches": None, "submodel": None}]
    taken = [{"step": 1, "brick_count": 2, "parts": [part]}]
    if attachment:
        steps.append({"index": 2, "attaches": "unit", "submodel": None})
        taken.append({"step": 2, "brick_count": 0, "parts": []})
        (book / "steps/step_0002.png").write_bytes(b"fixture attachment")
    save(run / "run_identity.json", {"source_sha256": sha})
    save(run / "plan.json", {"steps": steps})
    save(run / "bom.json", {"brick_count": 2, "steps": taken})
    save(run / "stability_summary.json", {"status": "FAILED", "static_risk_steps": [1]})
    result = run / "workflow_result.json"
    save(result, {"input_sha256": sha, "booklet_complete": True,
                  "files": {"pdf": str(book / "instructions_cn.pdf")}})
    return result


class InstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / "model.mpd"
        self.model.write_text("0 current model")
        self.service = instructions.InstructionService(self.root)

    def test_exact_model_cache_and_per_step_parts(self):
        fixture(self.root, self.model)
        state = self.service.status(self.model)
        self.assertEqual(state["state"], "ready")
        step = state["instructions"]["steps"][0]
        self.assertEqual(step["quantity"], 2)
        self.assertEqual(step["parts"][0]["name"], "薄板 1 × 2")
        self.assertEqual(step["parts"][0]["colour"], "白色")
        self.assertTrue(step["warning"])
        self.assertNotIn("total", state["instructions"])
        with patch("ui_instructions.subprocess.Popen", side_effect=AssertionError("cached booklet must not launch")):
            self.assertEqual(self.service.start(self.model)["state"], "ready")

    def test_same_path_changed_bytes_never_reuses_booklet(self):
        fixture(self.root, self.model)
        self.model.write_text("0 revised model")
        self.assertEqual(self.service.status(self.model)["state"], "missing")

    def test_incomplete_booklet_is_not_ready(self):
        result = fixture(self.root, self.model)
        (result.parent / "booklet/steps/step_0001.png").unlink()
        self.assertEqual(self.service.status(self.model)["state"], "missing")

    def test_declared_source_must_match_plan_identity(self):
        result = fixture(self.root, self.model)
        save(result.parent / "run_identity.json", {"source_sha256": "different"})
        self.assertEqual(self.service.status(self.model)["state"], "missing")

    def test_attachment_step_keeps_zero_parts(self):
        fixture(self.root, self.model, attachment=True)
        step = self.service.status(self.model)["instructions"]["steps"][1]
        self.assertEqual(step["quantity"], 0)
        self.assertEqual(step["parts"], [])
        self.assertEqual(step["attaches"], "unit")

    def test_assets_outside_project_are_rejected(self):
        result = fixture(self.root, self.model)
        row = json.loads(result.read_text())
        row["files"]["pdf"] = str(Path(__file__))
        save(result, row)
        self.assertEqual(self.service.status(self.model)["state"], "missing")

    def test_missing_environment_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "配置本机"):
            self.service.start(self.model)

    def test_double_click_launches_one_job_and_code_two_keeps_ready_booklet(self):
        wb = self.service.workbench
        (wb / ".venv/Scripts").mkdir(parents=True)
        (wb / ".venv/Scripts/python.exe").touch()
        (wb / "run.py").touch()
        save(wb / "config/local.json", {"output_root": str(self.root / "runs/lego-instructions")})
        launched, release = threading.Event(), threading.Event()

        class Process:
            def wait(process):
                launched.set()
                if not release.wait(5):
                    raise RuntimeError("test launch timed out")
                fixture(self.root, self.model)
                return 2

        with patch("ui_instructions.subprocess.Popen", return_value=Process()) as spawn:
            self.service.start(self.model)
            self.assertTrue(launched.wait(5))
            self.service.start(self.model)
            self.assertEqual(spawn.call_count, 1)
            release.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with self.service.lock:
                    state = self.service.jobs[instructions.digest(self.model)]["state"]
                if state in {"error", "complete"}:
                    break
                time.sleep(.02)
            self.assertEqual(state, "complete")
            self.assertEqual(self.service.status(self.model)["state"], "ready")


if __name__ == "__main__":
    unittest.main()
