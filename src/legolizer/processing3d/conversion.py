from __future__ import annotations

from pathlib import Path

import numpy as np
import open3d as o3d

from legolizer.processing3d.mesh import cleanup_mesh, read_mesh, write_mesh


def mesh_to_point_cloud(
    mesh_path: str | Path,
    output_path: str | Path,
    number_of_points: int = 50_000,
    seed: int = 0,
    source_unit_mm: float = 1.0,
) -> o3d.geometry.PointCloud:
    if number_of_points <= 0:
        raise ValueError("number_of_points 必须大于 0")
    o3d.utility.random.seed(seed)
    mesh = read_mesh(mesh_path)
    if source_unit_mm <= 0:
        raise ValueError("source_unit_mm 必须大于 0")
    if source_unit_mm != 1.0:
        mesh.scale(source_unit_mm, center=(0.0, 0.0, 0.0))
    mesh.compute_vertex_normals()
    point_cloud = mesh.sample_points_poisson_disk(number_of_points=number_of_points)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(target), point_cloud):
        raise RuntimeError(f"点云写出失败: {target}")
    return point_cloud


def _prepare_normals(
    point_cloud: o3d.geometry.PointCloud,
    normal_radius: float | None,
    normal_max_nn: int,
) -> float:
    points = np.asarray(point_cloud.points)
    if len(points) < 3 or not np.isfinite(points).all():
        raise ValueError("点云必须至少包含 3 个有限点")
    diagonal = float(np.linalg.norm(point_cloud.get_axis_aligned_bounding_box().get_extent()))
    radius = normal_radius or max(diagonal * 0.02, np.finfo(float).eps)
    point_cloud.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=normal_max_nn)
    )
    if len(points) >= 10:
        point_cloud.orient_normals_consistent_tangent_plane(min(50, len(points) - 1))
    return radius


def point_cloud_to_mesh(
    point_cloud_path: str | Path,
    output_path: str | Path,
    method: str = "poisson",
    poisson_depth: int = 8,
    density_quantile: float = 0.01,
    normal_radius: float | None = None,
    normal_max_nn: int = 30,
    bpa_radius_multipliers: tuple[float, ...] = (1.5, 3.0),
    source_unit_mm: float = 1.0,
) -> o3d.geometry.TriangleMesh:
    point_cloud = o3d.io.read_point_cloud(str(point_cloud_path))
    if source_unit_mm <= 0:
        raise ValueError("source_unit_mm 必须大于 0")
    if source_unit_mm != 1.0:
        point_cloud.scale(source_unit_mm, center=(0.0, 0.0, 0.0))
    radius = _prepare_normals(point_cloud, normal_radius, normal_max_nn)

    if method == "poisson":
        mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
            point_cloud, depth=poisson_depth
        )
        density_values = np.asarray(densities)
        if not 0 <= density_quantile < 1:
            raise ValueError("density_quantile 必须位于 [0, 1)")
        if density_quantile > 0:
            mesh.remove_vertices_by_mask(
                density_values < np.quantile(density_values, density_quantile)
            )
        bbox = point_cloud.get_axis_aligned_bounding_box()
        bbox = bbox.scale(1.05, bbox.get_center())
        mesh = mesh.crop(bbox)
    elif method == "ball-pivoting":
        distances = np.asarray(point_cloud.compute_nearest_neighbor_distance())
        base_radius = float(np.mean(distances)) if len(distances) else radius
        radii = o3d.utility.DoubleVector(
            [base_radius * multiplier for multiplier in bpa_radius_multipliers]
        )
        mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
            point_cloud, radii
        )
    else:
        raise ValueError(f"不支持的点云重建方法: {method}")

    cleanup_mesh(mesh)
    write_mesh(output_path, mesh)
    return mesh
