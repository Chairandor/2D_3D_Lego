"""Artifact parsing for the legolization search-tier comparison."""

import json
import tempfile
import unittest
from pathlib import Path

from examples.compare_legolization_quality import enrich_result


class QualityComparisonTests(unittest.TestCase):
    def test_partial_bundle_retains_actual_model_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legolization"
            for subdir in ("bom", "model", "instructions"):
                (root / subdir).mkdir(parents=True)
            (root / "bundle.json").write_text(json.dumps({
                "status": "partial",
                "verdicts": {"buildable": True, "stable": True,
                             "winner": {"strategy": "greedy", "seed": 0}},
                "stages": {"candidates": {"detail": {
                    "candidate_count": 1, "completed": 1, "pending": 0,
                    "exact_included": False, "time_budget_s": 90.0}}},
            }), encoding="utf-8")
            (root / "bom/bom.json").write_text(
                json.dumps({"brick_count": 2, "mass_g": 1.0}), encoding="utf-8")
            (root / "model/model.ldr").write_text(
                "0 model\n1 4 0 -24 0 1 0 0 0 1 0 0 0 1 3005.dat\n"
                "1 4 20 -24 0 1 0 0 0 1 0 0 0 1 3005.dat\n",
                encoding="utf-8")
            (root / "instructions/audit.json").write_text(json.dumps({
                "input": {"step_count": 1}, "certification": {"valid": True}}),
                encoding="utf-8")
            result = enrich_result({"bundle_status": "partial"}, Path(directory))
            self.assertEqual(result["bundle_status"], "partial")
            self.assertEqual(result["brick_count"], 2)
            self.assertTrue(result["brick_count_matches_ldraw"])
            self.assertEqual(result["candidate_plan"]["candidate_count"], 1)
            self.assertTrue(result["instruction_audit_valid"])


if __name__ == "__main__":
    unittest.main()
