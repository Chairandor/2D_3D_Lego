from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import open3d as o3d

from legolizer.integrations.backend import run_strict_backend
from legolizer.constants import BRICK_HEIGHT_MM, STUD_PITCH_MM
from legolizer.export.common import write_json
from legolizer.processing3d.mesh import (
    mesh_stats,
    normalize_mesh,
    orient_mesh_z_up,
    read_mesh,
    write_mesh,
)
from legolizer.processing3d.orientation import score_mesh_orientations
from legolizer.processing3d.source_frame import source_frame_rotation
from legolizer.processing3d.voxel import mesh_to_voxel
from legolizer.integrations.legolization import DEFAULT_PROJECT_DIR


def legoize_mesh(
    input_path: str | Path,
    output_dir: str | Path,
    target_width_studs: int = 24,
    fill: str = "orthographic",
    color: int = 4,
    use_source_colors: bool = True,
    source_up_axis: str = "z",
    legolization_quality: str = "fast",
    legolization_support: str = "table",
    legolization_hollow: bool = False,
    legolization_project_dir: str | Path = DEFAULT_PROJECT_DIR,
    legolization_duration_seconds: float | None = None,
    source_frame: str | None = None,
) -> dict:
    """将 Mesh 规范化为毫米制 Z-up，交给 legolization 并保留原始审计状态。"""
    if isinstance(target_width_studs, bool) or not isinstance(target_width_studs, int) or target_width_studs < 1:
        raise ValueError("target_width_studs 必须是正整数")
    started = time.perf_counter()
    source = Path(input_path).resolve()
    output = Path(output_dir)
    resolved_width = target_width_studs

    original = read_mesh(source)
    orientation: dict[str, object]
    if source_frame is not None:
        if source_up_axis != "z":
            raise ValueError("--source-frame 与 --source-up-axis 不能同时使用")
        source_to_z_up, orientation = source_frame_rotation(original, source_frame)
        resolved_up_axis = "y"
        oriented = o3d.geometry.TriangleMesh(original)
        oriented.transform(source_to_z_up)
    elif source_up_axis == "auto":
        orientation = score_mesh_orientations(original)
        selected_axis = orientation["selected_axis"]
        if orientation["ambiguous"] or not isinstance(selected_axis, str):
            candidates = orientation.get("candidates", [])
            rankings = ", ".join(
                f"{item['axis']}={item['score']:.3f}"
                for item in candidates[:3]
            )
            raise ValueError(
                f"无法可靠判断模型朝上方向（{orientation['reason']}；候选: {rankings}）。"
                "请用 --source-up-axis x|-x|y|-y|z|-z 明确指定。"
            )
        resolved_up_axis = selected_axis
        orientation["selection_source"] = "geometry_auto"
    else:
        resolved_up_axis = source_up_axis
        orientation = {
            "selected_axis": resolved_up_axis,
            "confidence": None,
            "ambiguous": False,
            "candidates": [],
            "reason": "用户显式指定；未执行几何评分",
            "selection_source": "user_manual",
        }
    if source_frame is None:
        oriented, source_to_z_up = orient_mesh_z_up(original, resolved_up_axis)
    recorded_source_up_axis = "y" if source_frame == "sf3d" else source_up_axis
    normalized, normalized_transform = normalize_mesh(
        oriented, target_width_mm=resolved_width * STUD_PITCH_MM
    )
    transform = normalized_transform @ source_to_z_up
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"输出目录必须为空，避免混入旧产物：{output}")
    output.mkdir(parents=True, exist_ok=True)
    normalized_path = output / "normalized_mesh.ply"
    write_mesh(normalized_path, normalized)
    write_json(
        output / "transform.json",
        {
            "source": str(source),
            "source_to_normalized": transform.tolist(),
            "unit": "mm",
            "coordinate_system": "right-handed, Z-up",
            "source_up_axis": recorded_source_up_axis,
            "source_frame": source_frame,
            "resolved_up_axis": resolved_up_axis,
            "orientation_selection_source": orientation["selection_source"],
            "orientation": orientation,
            "target_width_studs": resolved_width,
        },
    )

    volume = mesh_to_voxel(
        normalized,
        voxel_size_mm=(STUD_PITCH_MM, STUD_PITCH_MM, BRICK_HEIGHT_MM),
        fill=fill,
        source=str(normalized_path.resolve()),
    )
    if not use_source_colors and volume.colors is not None:
        volume = replace(volume, colors=None)
    volume.save(output / "source_occupancy.npz")
    strict = run_strict_backend(
        volume,
        output,
        quality=legolization_quality,
        support_mode=legolization_support,
        hollow=legolization_hollow,
        project_dir=legolization_project_dir,
        duration_seconds=legolization_duration_seconds,
        color=color,
    )
    report = {
        "pipeline": "mesh-to-lego-legolization-v1",
        "backend": "legolization",
        "source": str(source),
        "source_up_axis": recorded_source_up_axis,
        "source_frame": source_frame,
        "resolved_up_axis": resolved_up_axis,
        "orientation_selection_source": orientation["selection_source"],
        "orientation": orientation,
        "target_width_studs": resolved_width,
        "mesh": mesh_stats(normalized),
        "voxel": {
            "shape": list(volume.occupancy.shape),
            "occupied_cells": int(volume.occupancy.sum()),
            "voxel_size_mm": list(volume.voxel_size_mm),
            "origin_mm": list(volume.origin_mm),
            "semantic": volume.semantic,
        },
        "legolization": strict,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    write_json(output / "report.json", report)
    return report
