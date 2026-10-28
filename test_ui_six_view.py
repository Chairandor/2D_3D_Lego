from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageChops, ImageDraw

import ui_agent_jobs
import ui_assistant
import ui_revisions
import ui_six_view

ROOT = Path(__file__).resolve().parent


def sheet_bytes(colour=(230, 180, 40), size=(96, 64)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, colour).save(stream, format="PNG")
    return stream.getvalue()


def write_six_view(run: Path, size=(96, 64), rgba=False) -> tuple[Image.Image, Image.Image]:
    directory = run / "unitex"
    directory.mkdir(parents=True, exist_ok=True)
    original = Image.new("RGB", size, (128, 128, 128))
    mask = Image.new("L", size, 0)
    image_draw, mask_draw = ImageDraw.Draw(original), ImageDraw.Draw(mask)
    width, height = size[0] // 3, size[1] // 2
    for row in range(2):
        for column in range(3):
            box = (column * width + 6, row * height + 6, (column + 1) * width - 6, (row + 1) * height - 6)
            image_draw.rectangle(box, fill=(244, 235, 220))
            mask_draw.rectangle(box, fill=255)
    # A partial-coverage pixel and an internal silhouette boundary must both
    # retain the original RGB, not just the original binary bounding box.
    mask.putpixel((5, 6), 128)
    original.putpixel((5, 6), (186, 181, 174))
    mask.putpixel((15, 15), 0)
    original.putpixel((15, 15), (128, 128, 128))
    if rgba:
        original = original.convert("RGBA")
        original.putalpha(mask)
    original.save(directory / "mv_rgb.png")
    mask.save(directory / "mv_alpha.png")
    return original, mask


class _Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size=-1): return self.payload if size < 0 else self.payload[:size]


class SixViewTests(unittest.TestCase):
    def test_full_redraw_restores_every_boundary_background_and_alpha_pixel(self):
        for rgba in (False, True):
            with self.subTest(rgba=rgba), tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                root = Path(temporary)
                original, mask = write_six_view(root, rgba=rgba)
                reference = ui_six_view.resolve_reference(root, ROOT)
                proposed = Image.new("RGBA", original.size, (0, 255, 0, 0))
                result, report = reference.lock(proposed)
                self.assertEqual(result.size, original.size)
                editable = ui_six_view._editable_mask(mask)
                protected = ImageChops.invert(editable)
                difference = ImageChops.difference(result.convert("RGB"), original.convert("RGB"))
                self.assertIsNone(Image.composite(difference, Image.new("RGB", original.size), protected).getbbox())
                self.assertEqual(result.getpixel((6, 6)), original.getpixel((6, 6)))
                self.assertEqual(result.getpixel((5, 6)), original.getpixel((5, 6)))
                self.assertEqual(result.getpixel((15, 15)), original.getpixel((15, 15)))
                self.assertEqual(result.getpixel((10, 10))[:3], (0, 255, 0))
                if rgba:
                    self.assertEqual(result.getchannel("A").tobytes(), original.getchannel("A").tobytes())
                self.assertEqual(report["protected_pixels_changed"], 0)

    def test_repeated_edit_ignores_branch_mask_and_preserves_frozen_first_mask(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            original, _ = write_six_view(root)
            reference = ui_six_view.resolve_reference(root, ROOT)
            for relative in ("input/foreground.png", "hunyuan/mesh_shape_review_clean.glb"):
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_bytes(b"source")
            candidate = ui_revisions.add_candidate(root, "unitex/mv_rgb.png", "edit.png", sheet_bytes(), ROOT)
            branch = ui_revisions.create_texture_branch(
                root, Path(candidate["candidate"]), root / "input/foreground.png",
                root / "hunyuan/mesh_shape_review_clean.glb", ROOT,
            )
            write_six_view(branch)
            Image.new("L", original.size, 255).save(branch / "unitex/mv_alpha.png")
            Image.new("L", original.size, 255).save(root / "unitex/mv_alpha.png")
            Image.new("RGB", original.size, "black").save(root / "unitex/mv_rgb.png")
            repeated = ui_six_view.resolve_reference(branch, ROOT)
            self.assertEqual(repeated.image_sha256, reference.image_sha256)
            self.assertEqual(repeated.mask_sha256, reference.mask_sha256)
            self.assertEqual(repeated.original.tobytes(), original.tobytes())
            self.assertEqual(repeated.source_run, root)

    def test_uploads_are_constrained_and_record_the_reference_used(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            original, _ = write_six_view(root)
            for origin in ("assistant", "upload"):
                candidate = ui_revisions.add_candidate(
                    root, "unitex/mv_rgb.png", "edit.png", sheet_bytes((0, 255, 0)), ROOT, origin=origin,
                )
                with Image.open(candidate["candidate"]) as result:
                    self.assertEqual(result.getpixel((0, 0)), original.getpixel((0, 0)))
                    self.assertEqual(result.getpixel((10, 10)), (0, 255, 0))
                record = ui_revisions.selected_pair(root, "unitex/mv_rgb.png", ROOT)
                self.assertEqual(record["six_view_lock"]["policy"], ui_six_view.POLICY)
                self.assertEqual(record["six_view_lock"]["protected_pixels_changed"], 0)
            with self.assertRaisesRegex(ValueError, "尺寸"):
                ui_revisions.add_candidate(root, "unitex/mv_rgb.png", "edit.png", sheet_bytes(size=(48, 32)), ROOT)

    def test_missing_or_tampered_mask_is_rejected_instead_of_guessing(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            (root / "unitex").mkdir()
            (root / "unitex/mv_rgb.png").write_bytes(sheet_bytes())
            with self.assertRaisesRegex(ValueError, "缺少"):
                ui_six_view.resolve_reference(root, ROOT)
            write_six_view(root)
            ui_six_view.resolve_reference(root, ROOT)
            (root / "ui_revisions/six_view_reference/mask.png").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "已发生变化"):
                ui_six_view.resolve_reference(root, ROOT)

    def test_new_input_branch_cannot_borrow_previous_geometrys_mask(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            write_six_view(root)
            image = root / "source.png"
            image.write_bytes(sheet_bytes())
            branch = ui_revisions.create_input_branch(root, image, ROOT)
            with self.assertRaisesRegex(ValueError, "找不到最初"):
                ui_six_view.resolve_reference(branch, ROOT)

    def test_adopting_an_old_unlocked_candidate_cannot_bypass_policy(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            write_six_view(root)
            candidate = root / "old_candidate.png"
            candidate.write_bytes(sheet_bytes((0, 255, 0)))
            ui_revisions._save_state(root, {"version": 1, "pairs": {
                "unitex/mv_rgb.png": {
                    "original": str(root / "unitex/mv_rgb.png"),
                    "candidate": str(candidate), "choice": None, "origin": "assistant",
                },
            }})
            with self.assertRaisesRegex(ValueError, "不符合固定轮廓"):
                ui_revisions.choose(root, "unitex/mv_rgb.png", "candidate", ROOT)
            self.assertIsNone(ui_revisions.selected_pair(root, "unitex/mv_rgb.png", ROOT)["choice"])

    def test_api_edit_of_asset_without_adjacent_mask_is_still_locked(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            original, _ = write_six_view(root, size=(768, 512))
            reference = ui_six_view.resolve_reference(root, ROOT)
            source = root / "assets/edited.png"
            source.parent.mkdir()
            source.write_bytes(sheet_bytes((0, 0, 0), size=original.size))
            sent = []

            def fake_open(request, timeout):
                if isinstance(request, str):
                    return _Response(sheet_bytes((0, 255, 0), size=original.size))
                payload = json.loads(request.data)
                sent.append(payload)
                encoded = payload["input"]["messages"][0]["content"][0]["image"].split(",", 1)[1]
                with Image.open(io.BytesIO(base64.b64decode(encoded))) as normalized:
                    self.assertEqual(normalized.getpixel((0, 0)), original.getpixel((0, 0)))
                return _Response(json.dumps({"output": {"choices": [{"message": {"content": [{
                    "image": "https://example.aliyuncs.com/result.png",
                }]}}]}}).encode())

            with mock.patch.object(ui_agent_jobs, "_safe_key", return_value="test"), \
                 mock.patch.object(ui_agent_jobs.urllib.request, "urlopen", side_effect=fake_open):
                content = ui_agent_jobs._image_edit(source, "修正颜色", "unitex/mv_rgb.png", reference=reference)
            with Image.open(io.BytesIO(content)) as result:
                self.assertEqual(result.getpixel((0, 0)), original.getpixel((0, 0)))
                self.assertEqual(result.getpixel((10, 10)), (0, 255, 0))
            self.assertEqual(sent[0]["parameters"]["size"], "768*512")
            self.assertIn(ui_six_view.RULES, sent[0]["input"]["messages"][0]["content"][1]["text"])

    def test_unsupported_api_size_is_rejected_before_request(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            write_six_view(root)
            reference = ui_six_view.resolve_reference(root, ROOT)
            with mock.patch.object(ui_agent_jobs.urllib.request, "urlopen") as request:
                with self.assertRaisesRegex(ValueError, "拒绝自动缩放"):
                    ui_agent_jobs._image_edit(root / "unitex/mv_rgb.png", "修颜色", "unitex/mv_rgb.png", reference=reference)
            request.assert_not_called()

    def test_wrong_api_result_size_is_rejected_without_resizing(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary)
            write_six_view(root, size=(768, 512))
            reference = ui_six_view.resolve_reference(root, ROOT)
            api_answer = json.dumps({"output": {"choices": [{"message": {"content": [{
                "image": "https://example.aliyuncs.com/result.png",
            }]}}]}}).encode()
            with mock.patch.object(ui_agent_jobs, "_safe_key", return_value="test"), \
                 mock.patch.object(ui_agent_jobs.urllib.request, "urlopen", side_effect=[
                     _Response(api_answer), _Response(sheet_bytes(size=(512, 512))),
                 ]):
                with self.assertRaisesRegex(RuntimeError, "已拒绝"):
                    ui_agent_jobs._image_edit(root / "unitex/mv_rgb.png", "修颜色", "unitex/mv_rgb.png", reference=reference)

    def test_chat_receives_six_view_colour_only_policy(self):
        observed = []
        with mock.patch.object(ui_assistant, "_qwen", side_effect=lambda payload: observed.append(payload) or {"content": "只修颜色"}):
            ui_assistant.chat(run_dir=None, mode="stage", value="texture", message="修颜色", history=[], dispatch=lambda *args: {})
        self.assertIn(ui_six_view.RULES, observed[0]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
