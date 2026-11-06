"""体素到 legolization bundle 的执行与审计结果读取。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from legolizer.constants import BRICK_HEIGHT_MM, STUD_PITCH_MM
from legolizer.integrations.legolization import DEFAULT_PROJECT_DIR, export_legolization_npy, run_legolization_bundle
from legolizer.processing3d.volume import VoxelVolume
from legolizer.export.common import write_json


def run_strict_backend(
    volume: VoxelVolume,
    output_dir: str | Path,
    *,
    quality: str = "direct",
    support_mode: str = "table",
    hollow: bool = False,
    project_dir: str | Path = DEFAULT_PROJECT_DIR,
    duration_seconds: float | None = None,
    color: int = 4,
    placement_strategy: str | None = None,
    instruction_mode: str | None = None,
) -> dict[str, Any]:
    """以 Z-up、8×8×9.6 mm 体素运行严格后端，返回实际 bundle 判定。"""
    expected = (STUD_PITCH_MM, STUD_PITCH_MM, BRICK_HEIGHT_MM)
    if not np.allclose(volume.voxel_size_mm, expected, atol=1e-6):
        raise ValueError(
            f"严格后端需要 {expected} mm 体素；当前为 {volume.voxel_size_mm} mm"
        )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    input_npy = export_legolization_npy(
        volume, output / "legolization_input.npy", fallback_color=color
    )
    bundle_dir = output / "legolization"
    result = run_legolization_bundle(
        input_npy,
        bundle_dir,
        project_dir=project_dir,
        quality=quality,
        duration_seconds=duration_seconds,
        support_mode=support_mode,
        hollow=hollow,
        placement_strategy=placement_strategy,
        instruction_mode=instruction_mode,
    )
    bundle_path = bundle_dir / "bundle.json"
    if not bundle_path.is_file():
        raise RuntimeError(
            f"legolization 未生成 bundle.json；exit={result['exit_code']}"
        )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    verdicts = bundle.get("verdicts", {})
    model = bundle_dir / "model" / "model.mpd"
    audit_path = bundle_dir / "instructions" / "audit.json"
    audit = (
        json.loads(audit_path.read_text(encoding="utf-8"))
        if audit_path.is_file()
        else {}
    )
    certification = audit.get("certification", {})
    bom_path = bundle_dir / "bom" / "bom.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8")) if bom_path.is_file() else {}
    brick_count = (
        bom.get("brick_count")
        or audit.get("input", {}).get("brick_count")
        or bundle.get("stages", {}).get("generate", {}).get("detail", {}).get("brick_count")
    )
    summary = {
        "backend": "legolization",
        "quality": quality,
        "placement_strategy": placement_strategy,
        "instruction_mode": instruction_mode,
        "support_mode": support_mode,
        "physical_support_plate": False,
        "hollow": hollow,
        "source_occupied_cells": int(volume.occupancy.sum()),
        "source_shape": list(volume.occupancy.shape),
        "voxel_size_mm": list(volume.voxel_size_mm),
        "origin_mm": list(volume.origin_mm),
        "bundle_status": bundle.get("status"),
        "exit_code": result["exit_code"],
        "buildable": verdicts.get("buildable", False),
        "stable": verdicts.get("stable", False),
        "brick_count": brick_count,
        "instructions_status": bundle.get("stages", {})
        .get("instructions", {})
        .get("status"),
        "instruction_step_count": audit.get("input", {}).get("step_count"),
        "instruction_audit_valid": certification.get("valid"),
        "instruction_earliest_failure": certification.get("earliest_failure"),
        "model_path": str(model.resolve()) if model.is_file() else None,
        "bundle_path": str(bundle_path.resolve()),
        "instruction_audit_path": str(audit_path.resolve())
        if audit_path.is_file()
        else None,
        "warnings": bundle.get("warnings", []),
    }
    write_json(output / "backend_report.json", summary)
    return summary
