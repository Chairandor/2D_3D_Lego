from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import open3d as o3d


def download_redwood(output_dir: str | Path) -> Path:
    """下载 Open3D 五帧 Redwood 样例，并生成 LEGOLIZER manifest。"""
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    dataset = o3d.data.SampleRedwoodRGBDImages(data_root=str(output / "open3d_data"))
    trajectory = o3d.io.read_pinhole_camera_trajectory(dataset.trajectory_log_path)
    if len(trajectory.parameters) != len(dataset.color_paths):
        raise RuntimeError("Redwood 图像帧数与轨迹帧数不一致")

    def relative(value: str) -> str:
        return Path(os.path.relpath(value, output)).as_posix()

    payload = {
        "dataset": "Open3D SampleRedwoodRGBDImages",
        "intrinsic": relative(dataset.camera_intrinsic_path),
        "pose_convention": "camera_to_world",
        "depth_scale": 1000.0,
        "depth_trunc_m": 3.0,
        "convert_rgb_to_intensity": False,
        "frames": [
            {
                "color": relative(color),
                "depth": relative(depth),
                "pose": np.asarray(parameter.extrinsic).tolist(),
            }
            for color, depth, parameter in zip(
                dataset.color_paths, dataset.depth_paths, trajectory.parameters
            )
        ],
    }
    manifest = output / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest
