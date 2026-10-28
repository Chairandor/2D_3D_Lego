from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import ui_lego_details


class LegoDetailTests(unittest.TestCase):
    def test_cache_is_invalidated_when_same_model_path_changes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            model = Path(directory) / "model.mpd"
            model.write_text("0 original", encoding="utf-8")
            digest = ui_lego_details.fingerprint(model)
            cache = model.parent / ".ui-details"
            cache.mkdir()
            (cache / f"model-{digest}.bom.json").write_text(json.dumps({
                "source_sha256": digest, "total": [], "brick_count": 205,
            }), encoding="utf-8")
            self.assertEqual(ui_lego_details.cached_bom(model)["brick_count"], 205)
            model.write_text("0 modified", encoding="utf-8")
            self.assertIsNone(ui_lego_details.cached_bom(model))

    def test_project_boundary_and_model_format_are_checked(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "model.mpd").write_text("0 model", encoding="utf-8")
            (root / "notes.txt").write_text("notes", encoding="utf-8")
            self.assertEqual(ui_lego_details.model_path(str(root / "model.mpd"), root), root / "model.mpd")
            with self.assertRaises(ValueError):
                ui_lego_details.model_path(str(root / "notes.txt"), root)
            with self.assertRaises(ValueError):
                ui_lego_details.model_path(str(root / "model.mpd"), root / "other")

    def test_revision_does_not_inherit_original_models_comparison(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            original = Path(directory) / "lego-8" / "model.mpd"
            original.parent.mkdir()
            original.write_text("0 model", encoding="utf-8")
            candidate = Path(directory) / "ui_revisions" / "candidate.mpd"
            candidate.parent.mkdir()
            candidate.write_text("0 candidate", encoding="utf-8")
            self.assertIn("studs=8", ui_lego_details.describe(original, ldraw_available=False)["comparison_url"])
            self.assertIsNone(ui_lego_details.describe(candidate, ldraw_available=False)["comparison_url"])


if __name__ == "__main__":
    unittest.main()
