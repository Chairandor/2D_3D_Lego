"""按原始几何类型预览 Mesh、点云或体素产物。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import open3d as o3d

from legolizer.processing3d.colors import COMMON_LDRAW_COLORS
from legolizer.processing3d.mesh import orient_mesh_z_up
from legolizer.processing3d.orientation import orientation_matrix, score_mesh_orientations
from legolizer.processing3d.source_frame import source_frame_rotation
from legolizer.processing3d.volume import VoxelVolume


_FACES = (
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
)


def voxel_surface_mesh(volume: VoxelVolume) -> o3d.geometry.TriangleMesh:
    """按真实毫米坐标生成体素外露面，避免把体素预览成平滑表面。"""
    occupancy = volume.occupancy
    shape = occupancy.shape
    origin = np.asarray(volume.origin_mm, dtype=float)
    size = np.asarray(volume.voxel_size_mm, dtype=float)
    palette = {item.code: np.asarray(item.rgb, dtype=float) / 255.0 for item in COMMON_LDRAW_COLORS}
    vertices: list[np.ndarray] = []
    triangles: list[tuple[int, int, int]] = []
    colors: list[np.ndarray] = []
    default_color = np.asarray((0.32, 0.64, 0.91))
    for index in np.argwhere(occupancy):
        cell = tuple(int(value) for value in index)
        code = int(volume.colors[cell]) if volume.colors is not None else -1
        color = palette.get(code, default_color)
        for direction, corners in _FACES:
            neighbor = tuple(cell[axis] + direction[axis] for axis in range(3))
            if all(0 <= neighbor[axis] < shape[axis] for axis in range(3)) and occupancy[neighbor]:
                continue
            start = len(vertices)
            vertices.extend(origin + (index + corner) * size for corner in corners)
            triangles.extend(((start, start + 1, start + 2), (start, start + 2, start + 3)))
            colors.extend((color, color, color, color))
    mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(np.asarray(vertices)),
        o3d.utility.Vector3iVector(np.asarray(triangles, dtype=np.int32)),
    )
    mesh.vertex_colors = o3d.utility.Vector3dVector(np.asarray(colors))
    mesh.compute_vertex_normals()
    return mesh


def load_preview_geometry(
    path: str | Path, source_up_axis: str = "z", source_frame: str | None = None
) -> tuple[o3d.geometry.Geometry, dict[str, object]]:
    """读取 STL/PLY/NPZ；只在内存中将源上轴转换为 Z-up。"""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"预览文件不存在: {source}")
    if source_up_axis not in {"auto", "x", "-x", "y", "-y", "z", "-z"}:
        raise ValueError("source_up_axis 必须是 auto、±x、±y 或 ±z")
    if source_frame is not None and source_up_axis != "z":
        raise ValueError("--source-frame 与 --up-axis 不能同时使用")
    if source_frame is not None and source_frame != "sf3d":
        raise ValueError("source_frame 目前只支持 sf3d")
    suffix = source.suffix.lower()
    if suffix == ".npz":
        if source_frame is not None:
            raise ValueError("NPZ 已采用内部 Z-up；--source-frame 仅适用于 Mesh")
        volume = VoxelVolume.load(source)
        if source_up_axis != "z":
            raise ValueError("NPZ 体素采用项目内部 Z-up 坐标，请使用 --up-axis z")
        mesh = voxel_surface_mesh(volume)
        return mesh, {
            "type": "voxel",
            "occupied_cells": int(volume.occupancy.sum()),
            "shape": list(volume.occupancy.shape),
            "voxel_size_mm": list(volume.voxel_size_mm),
            "semantic": volume.semantic,
        }
    if suffix not in {".ply", ".stl"}:
        raise ValueError("preview 支持 .ply、.stl、.npz；.ldr 请用 BrickLink Studio 打开")

    if suffix == ".stl":
        kind = "mesh"
    else:
        with source.open("rb") as handle:
            header = handle.read(4096).split(b"end_header", 1)[0]
        kind = "mesh" if b"element face " in header else "pointcloud"

    if kind == "mesh":
        geometry = o3d.io.read_triangle_mesh(str(source))
        if not geometry.has_vertices() or not geometry.has_triangles():
            raise ValueError(f"网格为空或读取失败: {source}")
        orientation: dict[str, object] | None = None
        resolved_up_axis = source_up_axis
        if source_frame is not None:
            transform, orientation = source_frame_rotation(geometry, source_frame)
            geometry.transform(transform)
            resolved_up_axis = "y"
        elif source_up_axis == "auto":
            orientation = score_mesh_orientations(geometry)
            selected = orientation["selected_axis"]
            if isinstance(selected, str) and not orientation["ambiguous"]:
                resolved_up_axis = selected
        if source_frame is None and resolved_up_axis != "auto":
            geometry, _ = orient_mesh_z_up(geometry, resolved_up_axis)
        if not geometry.has_vertex_normals():
            geometry.compute_vertex_normals()
        info: dict[str, object] = {
            "type": "mesh",
            "vertices": len(geometry.vertices),
            "triangles": len(geometry.triangles),
        }
        if orientation is not None:
            info["orientation"] = orientation
        info["resolved_up_axis"] = resolved_up_axis if resolved_up_axis != "auto" else None
        if source_frame is not None:
            info["source_frame"] = source_frame
    else:
        if source_frame is not None:
            raise ValueError("点云 --source-frame 暂未实现；请先生成 Mesh")
        geometry = o3d.io.read_point_cloud(str(source))
        if not geometry.has_points():
            raise ValueError(f"点云为空或读取失败: {source}")
        if source_up_axis == "auto":
            raise ValueError("点云 auto 上轴评分尚未实现，请用 --up-axis 显式指定")
        geometry.transform(orientation_matrix(source_up_axis))
        info = {"type": "pointcloud", "points": len(geometry.points)}
        info["resolved_up_axis"] = source_up_axis
    extent = geometry.get_axis_aligned_bounding_box().get_extent()
    info["extent_input_units"] = np.round(extent, 4).tolist()
    info["display_up_axis"] = "z" if info["resolved_up_axis"] is not None else "undetermined"
    return geometry, info


def preview_file(
    path: str | Path, source_up_axis: str = "z", info_only: bool = False,
    source_frame: str | None = None,
) -> dict[str, object]:
    """预览产物；info_only 可用于无图形界面的格式与尺度检查。"""
    geometry, info = load_preview_geometry(path, source_up_axis, source_frame)
    if not info_only and info.get("display_up_axis") == "undetermined":
        orientation = info["orientation"]
        raise ValueError(
            f"自动上轴无法可靠判定（{orientation['reason']}）；"
            "请先用 --info 查看候选，再用 --up-axis 显式指定。"
        )
    if not info_only:
        print("关闭 Open3D 窗口后命令才会结束；鼠标左键旋转，滚轮缩放。", flush=True)
        o3d.visualization.draw_geometries(
            [geometry],
            window_name=f"LEGOLIZER Preview - {Path(path).name}",
            width=1100,
            height=760,
            mesh_show_back_face=True,
        )
    return info
