from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import open3d as o3d

from legolizer.processing3d.conversion import _prepare_normals
from legolizer.processing3d.mesh import cleanup_mesh, write_mesh


def _read_manifest(path: str | Path) -> tuple[dict[str, Any], Path]:
    manifest_path = Path(path).resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not data.get("frames"):
        raise ValueError("RGB-D manifest 没有 frames")
    convention = data.get("pose_convention")
    if convention not in {"camera_to_world", "world_to_camera"}:
        raise ValueError("pose_convention 必须是 camera_to_world 或 world_to_camera")
    return data, manifest_path.parent


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def _intrinsic(
    data: dict[str, Any],
    base: Path,
    frame: dict[str, Any] | None = None,
) -> o3d.camera.PinholeCameraIntrinsic:
    """读取相机内参；多相机 manifest 可在 frame 中覆盖全局内参。"""
    spec = frame.get("intrinsic", data["intrinsic"]) if frame else data["intrinsic"]
    if isinstance(spec, str):
        intrinsic = o3d.io.read_pinhole_camera_intrinsic(str(_resolve(base, spec)))
        if intrinsic.width <= 0:
            raise ValueError(f"无法读取相机内参: {spec}")
        return intrinsic
    required = ("width", "height", "fx", "fy", "cx", "cy")
    missing = [key for key in required if key not in spec]
    if missing:
        raise ValueError(f"内参缺少字段: {', '.join(missing)}")
    return o3d.camera.PinholeCameraIntrinsic(
        int(spec["width"]),
        int(spec["height"]),
        float(spec["fx"]),
        float(spec["fy"]),
        float(spec["cx"]),
        float(spec["cy"]),
    )


def _rgbd(frame: dict[str, Any], data: dict[str, Any], base: Path) -> o3d.geometry.RGBDImage:
    color = o3d.io.read_image(str(_resolve(base, frame["color"])))
    depth = o3d.io.read_image(str(_resolve(base, frame["depth"])))
    if np.asarray(color).size == 0 or np.asarray(depth).size == 0:
        raise ValueError(f"RGB-D 图像读取失败: {frame}")
    return o3d.geometry.RGBDImage.create_from_color_and_depth(
        color,
        depth,
        depth_scale=float(data.get("depth_scale", 1000.0)),
        depth_trunc=float(data.get("depth_trunc_m", 3.0)),
        convert_rgb_to_intensity=bool(data.get("convert_rgb_to_intensity", False)),
    )


def _camera_to_world(frame: dict[str, Any], convention: str) -> np.ndarray:
    pose = np.asarray(frame.get("pose", np.eye(4)), dtype=float)
    if pose.shape != (4, 4) or not np.isfinite(pose).all():
        raise ValueError("每帧 pose 必须是有限的 4x4 矩阵")
    return pose if convention == "camera_to_world" else np.linalg.inv(pose)


def rgbd_to_point_cloud(
    manifest_path: str | Path,
    output_path: str | Path,
    voxel_downsample_mm: float | None = None,
) -> o3d.geometry.PointCloud:
    data, base = _read_manifest(manifest_path)
    merged = o3d.geometry.PointCloud()
    for frame in data["frames"]:
        intrinsic = _intrinsic(data, base, frame)
        cloud = o3d.geometry.PointCloud.create_from_rgbd_image(
            _rgbd(frame, data, base), intrinsic
        )
        cloud.transform(_camera_to_world(frame, data["pose_convention"]))
        merged += cloud
    if voxel_downsample_mm:
        merged = merged.voxel_down_sample(voxel_downsample_mm / 1000.0)
    if len(merged.points) == 0:
        raise ValueError("RGB-D 反投影结果为空")
    merged.scale(1000.0, center=(0.0, 0.0, 0.0))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(target), merged):
        raise RuntimeError(f"点云写出失败: {target}")
    return merged


def rgbd_to_tsdf_mesh(
    manifest_path: str | Path,
    output_path: str | Path,
    voxel_length_m: float = 0.004,
    sdf_trunc_m: float = 0.02,
) -> o3d.geometry.TriangleMesh:
    data, base = _read_manifest(manifest_path)
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=voxel_length_m,
        sdf_trunc=sdf_trunc_m,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    for frame in data["frames"]:
        intrinsic = _intrinsic(data, base, frame)
        camera_to_world = _camera_to_world(frame, data["pose_convention"])
        volume.integrate(
            _rgbd(frame, data, base), intrinsic, np.linalg.inv(camera_to_world)
        )
    mesh = volume.extract_triangle_mesh()
    cleanup_mesh(mesh)
    # Open3D 的 RGB-D/TSDF 坐标单位是米，项目内部统一转换为毫米。
    mesh.scale(1000.0, center=(0.0, 0.0, 0.0))
    write_mesh(output_path, mesh)
    return mesh


def rgbd_to_poisson_mesh(
    manifest_path: str | Path,
    output_path: str | Path,
    poisson_depth: int = 8,
    density_quantile: float = 0.01,
) -> o3d.geometry.TriangleMesh:
    data, base = _read_manifest(manifest_path)
    merged = o3d.geometry.PointCloud()
    for frame in data["frames"]:
        intrinsic = _intrinsic(data, base, frame)
        cloud = o3d.geometry.PointCloud.create_from_rgbd_image(
            _rgbd(frame, data, base), intrinsic
        )
        cloud.transform(_camera_to_world(frame, data["pose_convention"]))
        merged += cloud
    merged = merged.voxel_down_sample(float(data.get("poisson_downsample_m", 0.003)))
    _prepare_normals(merged, normal_radius=None, normal_max_nn=30)
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        merged, depth=poisson_depth
    )
    density_values = np.asarray(densities)
    mesh.remove_vertices_by_mask(
        density_values < np.quantile(density_values, density_quantile)
    )
    mesh = mesh.crop(merged.get_axis_aligned_bounding_box())
    cleanup_mesh(mesh)
    mesh.scale(1000.0, center=(0.0, 0.0, 0.0))
    write_mesh(output_path, mesh)
    return mesh
