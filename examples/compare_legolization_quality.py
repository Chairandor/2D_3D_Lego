"""Run legolization fast/balanced/exhaustive on one unchanged voxel input.

These are search-effort tiers, not certified assembly-difficulty tiers. Each
run uses table contact, no emitted support and no hollowing for comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legolizer.integrations.backend import run_strict_backend
from legolizer.export.common import write_json
from legolizer.processing3d.volume import VoxelVolume
from legolizer.constants import DEFAULT_PROJECT_DIR


QUALITIES = ("fast", "balanced", "exhaustive")


def enrich_result(result: dict, level_output: Path) -> dict:
    """Read completed bundle artifacts without treating partial as success."""
    bundle_dir = level_output / "legolization"
    bundle_path = bundle_dir / "bundle.json"
    if bundle_path.is_file():
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        stages = bundle.get("stages", {})
        plan = stages.get("candidates", {}).get("detail", {})
        result["candidate_plan"] = {
            key: plan.get(key) for key in
            ("candidate_count", "completed", "pending", "exact_included",
             "exact_skip_reason", "time_budget_s", "variants")}
        result["winner"] = bundle.get("verdicts", {}).get("winner")
    bom_path = bundle_dir / "bom" / "bom.json"
    if bom_path.is_file():
        bom = json.loads(bom_path.read_text(encoding="utf-8"))
        result["brick_count"] = bom.get("brick_count")
        result["mass_g"] = bom.get("mass_g")
    model_path = bundle_dir / "model" / "model.ldr"
    if model_path.is_file():
        model_count = sum(line.startswith("1 ") for line in
                          model_path.read_text(encoding="utf-8").splitlines())
        result["flat_ldraw_brick_count"] = model_count
        result["brick_count_matches_ldraw"] = result.get("brick_count") == model_count
    audit_path = bundle_dir / "instructions" / "audit.json"
    if audit_path.is_file():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        result["instruction_step_count"] = audit.get("input", {}).get("step_count")
        result["instruction_audit_valid"] = audit.get("certification", {}).get("valid")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_occupancy", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--duration-seconds", type=float, default=90.0)
    parser.add_argument("--qualities", nargs="+", choices=QUALITIES,
                        default=list(QUALITIES))
    parser.add_argument("--project-dir", type=Path,
                        default=DEFAULT_PROJECT_DIR)
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")
    source_path = args.source_occupancy.resolve()
    if not source_path.is_file():
        parser.error(f"source voxel file not found: {source_path}")
    volume = VoxelVolume.load(source_path)
    sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for quality in args.qualities:
        level_output = output / quality
        if level_output.exists() and any(level_output.iterdir()):
            parser.error(f"refusing to overwrite nonempty {level_output}")
        started = time.perf_counter()
        try:
            result = run_strict_backend(
                volume, level_output, quality=quality, support_mode="table",
                hollow=False, project_dir=args.project_dir,
                duration_seconds=args.duration_seconds, color=4)
            result["run_status"] = "returned"
        except Exception as exc:
            result = {"run_status": "error", "error_type": type(exc).__name__,
                      "error": str(exc), "quality": quality}
        result.update({
            "source": str(source_path), "source_sha256": sha256,
            "source_occupied_cells": int(volume.occupancy.sum()),
            "source_shape": list(volume.occupancy.shape),
            "time_budget_seconds": args.duration_seconds,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "support_mode": "table", "hollow": False,
            "emitted_support": False,
        })
        result = enrich_result(result, level_output)
        level_output.mkdir(parents=True, exist_ok=True)
        write_json(level_output / "quality_result.json", result)
        print(json.dumps({key: result.get(key) for key in
                          ("quality", "run_status", "bundle_status", "exit_code",
                           "brick_count", "buildable", "stable", "elapsed_seconds")},
                         ensure_ascii=False), flush=True)
    collected = {}
    for quality in QUALITIES:
        path = output / quality / "quality_result.json"
        if path.is_file():
            value = enrich_result(json.loads(path.read_text(encoding="utf-8")),
                                  output / quality)
            write_json(path, value)
            collected[quality] = value
    write_json(output / "comparison.json", {
        "source": str(source_path), "source_sha256": sha256,
        "time_budget_seconds": args.duration_seconds,
        "support_mode": "table", "hollow": False,
        "note": "quality tiers change search policy, not user assembly difficulty",
        "results": collected,
    })


if __name__ == "__main__":
    main()
