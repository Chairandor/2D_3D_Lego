from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import open3d as o3d

from legolizer.processing3d.orientation import orientation_matrix


def read_mesh(path: str | Path) -> o3d.geometry.TriangleMesh:
    mesh = o3d.io.read_triangle_mesh(str(path), enable_post_processing=False)
    validate_mesh(mesh, label=str(path))
    return mesh


def write_mesh(path: str | Path, mesh: o3d.geometry.TriangleMesh) -> None:
    validate_mesh(mesh, label="待写出网格")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_triangle_mesh(str(target), mesh, write_ascii=False):
        raise RuntimeError(f"网格写出失败: {target}")


def validate_mesh(mesh: o3d.geometry.TriangleMesh, label: str = "mesh") -> None:
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)
    if len(vertices) == 0 or len(triangles) == 0:
        raise ValueError(f"{label} 不包含有效顶点或三角形")
    if not np.isfinite(vertices).all():
        raise ValueError(f"{label} 包含 NaN 或无穷坐标")


def cleanup_mesh(mesh: o3d.geometry.TriangleMesh) -> o3d.geometry.TriangleMesh:
    mesh.remove_duplicated_vertices()
    mesh.remove_duplicated_triangles()
    mesh.remove_degenerate_triangles()
    mesh.remove_non_manifold_edges()
    mesh.remove_unreferenced_vertices()
    mesh.compute_vertex_normals()
    validate_mesh(mesh)
    return mesh


def mesh_stats(mesh: o3d.geometry.TriangleMesh) -> dict[str, Any]:
    vertices = np.asarray(mesh.vertices)
    bbox = mesh.get_axis_aligned_bounding_box()
    clusters, counts, _ = mesh.cluster_connected_triangles()
    del clusters
    return {
        "vertices": len(mesh.vertices),
        "triangles": len(mesh.triangles),
        "bounds_min": bbox.get_min_bound().tolist(),
        "bounds_max": bbox.get_max_bound().tolist(),
        "extent_mm": bbox.get_extent().tolist(),
        "finite": bool(np.isfinite(vertices).all()),
        "edge_manifold": bool(mesh.is_edge_manifold(allow_boundary_edges=True)),
        "edge_manifold_closed": bool(mesh.is_edge_manifold(allow_boundary_edges=False)),
        "vertex_manifold": bool(mesh.is_vertex_manifold()),
        "watertight": bool(mesh.is_watertight()),
        "orientable": bool(mesh.is_orientable()),
        "connected_components": int(len(counts)),
    }


def normalize_mesh(
    mesh: o3d.geometry.TriangleMesh,
    target_width_mm: float,
) -> tuple[o3d.geometry.TriangleMesh, np.ndarray]:
    """按 XY 最大尺寸缩放并把包围盒最小角平移到原点。"""
    if target_width_mm <= 0:
        raise ValueError("target_width_mm 必须大于 0")
    result = o3d.geometry.TriangleMesh(mesh)
    bounds = result.get_axis_aligned_bounding_box()
    extent = bounds.get_extent()
    horizontal = float(max(extent[0], extent[1]))
    if horizontal <= 0:
        raise ValueError("网格水平尺寸为 0，无法归一化")
    scale = target_width_mm / horizontal
    minimum = bounds.get_min_bound()
    transform = np.eye(4)
    transform[:3, :3] *= scale
    transform[:3, 3] = -minimum * scale
    result.transform(transform)
    cleanup_mesh(result)
    return result, transform


def orient_mesh_z_up(
    mesh: o3d.geometry.TriangleMesh,
    source_up_axis: str = "z",
) -> tuple[o3d.geometry.TriangleMesh, np.ndarray]:
    """将指定源上轴转为 Z-up；返回新 Mesh 和源到 Z-up 的 4×4 变换。"""
    transform = orientation_matrix(source_up_axis)
    result = o3d.geometry.TriangleMesh(mesh)
    result.transform(transform)
    return result, transform
