from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import ui_agent_jobs
import ui_assistant
import ui_revisions
from pipeline_ui import ROOT


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.payload if size < 0 else self.payload[:size]


class AssistantTests(unittest.TestCase):
    def test_image_output_size_follows_api_rounding(self) -> None:
        self.assertEqual(ui_agent_jobs._image_output_size((312, 312)), (512, 512))
        self.assertEqual(ui_agent_jobs._image_output_size((1033, 1032)), (1040, 1024))
        self.assertEqual(ui_agent_jobs._image_output_size((3000, 3000)), (2048, 2048))

    def test_image_edit_uses_native_dashscope_and_preserves_dimensions(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            source = Path(temporary) / "foreground.png"
            image = Image.new("RGBA", (512, 512), (255, 100, 0, 0))
            image.putpixel((256, 256), (255, 100, 0, 255))
            image.save(source)
            generated = io.BytesIO()
            edited_image = Image.new("RGBA", (512, 512), (255, 120, 0, 0))
            edited_image.putpixel((256, 256), (255, 120, 0, 255))
            edited_image.save(generated, "PNG")
            calls = []

            def fake_open(request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    body = json.loads(request.data)
                    self.assertIn("/multimodal-generation/generation", request.full_url)
                    self.assertEqual(body["input"]["messages"][0]["role"], "user")
                    return _Response(json.dumps({"output": {"choices": [{"message": {"content": [
                        {"image": "https://example.aliyuncs.com/generated.png"}
                    ]}}]}}).encode())
                return _Response(generated.getvalue())

            with mock.patch.object(ui_agent_jobs, "_safe_key", return_value="test"), \
                 mock.patch.object(ui_agent_jobs.urllib.request, "urlopen", side_effect=fake_open):
                result = ui_agent_jobs._image_edit(source, "不改变形状", "input/foreground.png")
            self.assertEqual(len(calls), 2)
            with Image.open(io.BytesIO(result)) as edited:
                self.assertEqual(edited.size, (512, 512))

    def test_small_image_is_sent_unchanged_and_result_returns_to_original_size(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            source = Path(temporary) / "foreground.png"
            Image.new("RGBA", (312, 312), (255, 100, 0, 255)).save(source)
            generated = io.BytesIO()
            Image.new("RGBA", (512, 512), (255, 120, 0, 255)).save(generated, "PNG")
            calls = []

            def fake_open(request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    body = json.loads(request.data)
                    self.assertEqual(body["parameters"]["size"], "512*512")
                    encoded = body["input"]["messages"][0]["content"][0]["image"].split(",", 1)[1]
                    with Image.open(io.BytesIO(base64.b64decode(encoded))) as sent:
                        self.assertEqual(sent.size, (312, 312))
                    return _Response(json.dumps({"output": {"choices": [{"message": {"content": [
                        {"image": "https://example.aliyuncs.com/generated.png"}
                    ]}}]}}).encode())
                return _Response(generated.getvalue())

            with mock.patch.object(ui_agent_jobs, "_safe_key", return_value="test"), \
                 mock.patch.object(ui_agent_jobs.urllib.request, "urlopen", side_effect=fake_open):
                result = ui_agent_jobs._image_edit(source, "去掉叶子", "input/foreground.png")
            self.assertEqual(len(calls), 2)
            with Image.open(io.BytesIO(result)) as edited:
                self.assertEqual(edited.size, (312, 312))

    def test_chat_tool_call_dispatches_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            calls = []
            answer = {"tool_calls": [{"function": {
                "name": "run_lego_skill",
                "arguments": json.dumps({"skill": "analyze-lego-assembly"}),
            }}]}
            with mock.patch.object(ui_assistant, "_qwen", return_value=answer):
                result = ui_assistant.chat(
                    run_dir=Path(temporary), mode="stage", value="lego",
                    message="检查稳定性", history=[],
                    dispatch=lambda name, args: calls.append((name, args)) or {"id": "test"},
                )
            self.assertEqual(calls, [("run_lego_skill", {"skill": "analyze-lego-assembly"})])
            self.assertIn("报告或产物", result["answer"])

    def test_assistant_candidate_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            run = root / "run"
            original = run / "unitex" / "mv_rgb.png"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"original")
            result = ui_revisions.add_candidate(
                run, "unitex/mv_rgb.png", "assistant.png", b"candidate", root,
                origin="assistant",
            )
            self.assertEqual(original.read_bytes(), b"original")
            self.assertEqual(Path(str(result["candidate"])).read_bytes(), b"candidate")
            self.assertIsNone(ui_revisions.selected_pair(run, "unitex/mv_rgb.png", root)["choice"])

    def test_new_lego_job_stays_in_isolated_path_and_can_be_analyzed(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            run = root / "run"
            model = run / "ui_assistant" / "jobs" / "20261023-120000-abcdef01" / "bundle" / "model" / "model.mpd"
            model.parent.mkdir(parents=True)
            model.write_text("0 FILE model.mpd", encoding="utf-8")
            (model.parents[2] / "request.json").write_text(
                json.dumps({"skill": "legolize-model", "studs": 16,
                            "source_relative": "lego-16/model.mpd"}), encoding="utf-8",
            )
            manager = ui_agent_jobs.AgentJobManager(root, root / "legolization.exe", root, root)
            self.assertEqual(manager._lego_model(run, 16)[0], model)
            self.assertEqual(ui_revisions.stage_for(model.relative_to(run).as_posix()), "lego")
            self.assertFalse((run / "lego-16" / "model.mpd").exists())
            second = run / "lego-8" / "model.mpd"
            second.parent.mkdir()
            second.write_text("0 FILE model.mpd", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "多个 LEGO 模型"):
                manager._lego_model(run, None)


if __name__ == "__main__":
    unittest.main()
