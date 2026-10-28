from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader

import ui_parts_pdf
import ui_lego_details


def fixture(count=13):
    image = io.BytesIO()
    Image.new("RGBA", (144, 108), (200, 130, 40, 255)).save(image, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
    rows = [{"part_key": "brick_1x1", "ldraw_part": "3005", "colour_code": i,
             "colour_name": "Orange", "quantity": i + 1} for i in range(count)]
    return {"title": "合成清单验收", "bom": {"total": rows, "brick_count": sum(r["quantity"] for r in rows)},
            "thumbnails": {f'3005:{r["colour_code"]}': url for r in rows}}


class PartsPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_paginated_pdf_contains_all_rows_without_booklet_sections(self):
        path = self.root / "list.pdf"
        data = fixture()
        result = ui_parts_pdf.render_pdf(data, path, Path(__file__).parent / "instructions_workbench/assets/fonts/chinese.ttf")
        self.assertEqual(result, {"pages": 2, "rows": 13, "quantity": 91})
        reader = PdfReader(path)
        self.assertEqual(len(reader.pages), 2)
        text = "\n".join(page.extract_text() for page in reader.pages)
        self.assertEqual(text.count("LDraw · 3005"), 13)
        self.assertIn("91 件", text)
        self.assertIn("颜色编号 · 12", text)
        self.assertNotIn("拼搭步骤", text)
        self.assertNotIn("成品展示", text)
        self.assertTrue(all(page.images for page in reader.pages))

    def test_changed_model_is_rejected_before_export(self):
        model = self.root / "model.mpd"
        model.write_text("0 revised")
        with patch("ui_lego_details.generate_bom", side_effect=AssertionError("must reject changed model")):
            with self.assertRaisesRegex(ValueError, "更新"):
                ui_parts_pdf.export_pdf(model, Path("unused"), self.root, self.root, "old", {}, "model")

    def test_export_requires_the_full_authoritative_bom_even_when_search_is_filtered(self):
        data = fixture(2)
        model = self.root / "model.mpd"
        model.write_text("0 model")
        images = {"3005:0": data["thumbnails"]["3005:0"]}
        with patch("ui_lego_details.generate_bom", return_value=data["bom"]):
            with self.assertRaisesRegex(ValueError, "尚未准备好"):
                ui_parts_pdf.export_pdf(model, Path("unused"), self.root, self.root,
                                        ui_lego_details.fingerprint(model), images, "model")
        self.assertFalse(list(self.root.glob(".ui-details/*.pdf")))

    def test_inconsistent_quantity_is_rejected(self):
        data = fixture(2)
        data["bom"]["brick_count"] = 100
        with self.assertRaisesRegex(ValueError, "数量"):
            ui_parts_pdf.render_pdf(data, self.root / "bad.pdf", Path("unused"))


if __name__ == "__main__":
    unittest.main()
