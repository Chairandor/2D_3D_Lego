from __future__ import annotations

from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree
import trimesh

from legolizer.processing3d.colors import quantize_rgb_to_ldraw
from legolizer.processing3d.mesh import cleanup_mesh, read_mesh, write_mesh
from legolizer.processing3d.volume import VoxelVolume


def _mesh_voxel_colors(
    mesh: o3d.geometry.TriangleMesh,
    grid: trimesh.voxel.VoxelGrid,
    occupancy: np.ndarray,
    voxel_size_mm: np.ndarray,
) -> np.ndarray | None:
    if not mesh.has_vertex_colors():
        return None
    vertices = np.asarray(mesh.vertices, dtype=float)
    vertex_colors = np.asarray(mesh.vertex_colors, dtype=float)
    if len(vertices) == 0 or vertex_colors.shape != vertices.shape:
        return None
    occupied_indices = np.argwhere(occupancy)
    centers_mm = grid.indices_to_points(occupied_indices) * voxel_size_mm
    _, nearest = cKDTree(vertices).query(centers_mm, workers=-1)
    codes = quantize_rgb_to_ldraw(vertex_colors[nearest]).astype(np.int16)
    colors = np.full(occupancy.shape, -1, dtype=np.int16)
    colors[tuple(occupied_indices.T)] = codes
    return colors


def mesh_to_voxel(
    mesh_or_path: o3d.geometry.TriangleMesh | str | Path,
    voxel_size_mm: tuple[float, float, float],
    fill: str = "orthographic",
    source: str | None = None,
) -> VoxelVolume:
    """将 Mesh 映射到可为各向异性的 XYZ 体素网格。"""
    mesh = read_mesh(mesh_or_path) if isinstance(mesh_or_path, (str, Path)) else mesh_or_path
    size = np.asarray(voxel_size_mm, dtype=float)
    if size.shape != (3,) or np.any(size <= 0):
        raise ValueError("voxel_size_mm 必须是三个正数")

    vertices = np.asarray(mesh.vertices, dtype=float) / size
    faces = np.asarray(mesh.triangles, dtype=np.int64)
    tri_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    grid = tri_mesh.voxelized(pitch=1.0)
    if fill == "surface":
        semantic = "surface"
    elif fill in {"holes", "orthographic"}:
        grid = grid.fill(method=fill)
        semantic = "solid" if fill == "orthographic" else "hole-filled"
    else:
        raise ValueError("fill 必须是 surface、holes 或 orthographic")

    occupancy = np.asarray(grid.matrix, dtype=bool)
    center_zero = np.asarray(grid.transform[:3, 3], dtype=float)
    origin = (center_zero - 0.5) * size
    colors = _mesh_voxel_colors(mesh, grid, occupancy, size)
    return VoxelVolume(
        occupancy=occupancy,
        voxel_size_mm=tuple(float(v) for v in size),
        origin_mm=tuple(float(v) for v in origin),
        semantic=semantic,
        source=source,
        colors=colors,
    )


def point_cloud_to_voxel(
    cloud_or_path: o3d.geometry.PointCloud | str | Path,
    voxel_size_mm: tuple[float, float, float],
    source_unit_mm: float = 1.0,
    source: str | None = None,
) -> VoxelVolume:
    """把点云离散为 XYZ 占用体素；同一体素内的颜色按多数票量化。"""
    if source_unit_mm <= 0:
        raise ValueError("source_unit_mm 必须大于 0")
    size = np.asarray(voxel_size_mm, dtype=float)
    if size.shape != (3,) or np.any(size <= 0):
        raise ValueError("voxel_size_mm 必须是三个正数")
    if isinstance(cloud_or_path, (str, Path)):
        cloud = o3d.io.read_point_cloud(str(cloud_or_path))
        source = source or str(Path(cloud_or_path).resolve())
    else:
        cloud = cloud_or_path
    points = np.asarray(cloud.points, dtype=float) * source_unit_mm
    if points.ndim != 2 or points.shape[1] != 3 or len(points) == 0:
        raise ValueError("点云为空或坐标格式无效")
    if not np.isfinite(points).all():
        raise ValueError("点云包含 NaN 或无穷坐标")

    origin = np.floor(points.min(axis=0) / size) * size
    indices = np.floor((points - origin) / size + 1e-9).astype(np.int64)
    shape = tuple((indices.max(axis=0) + 1).tolist())
    occupancy = np.zeros(shape, dtype=bool)
    occupancy[tuple(indices.T)] = True

    colors: np.ndarray | None = None
    if cloud.has_colors():
        point_colors = np.asarray(cloud.colors, dtype=float)
        if point_colors.shape == points.shape:
            point_codes = quantize_rgb_to_ldraw(point_colors).astype(np.int16)
            linear = np.ravel_multi_index(indices.T, shape)
            order = np.argsort(linear, kind="stable")
            sorted_linear = linear[order]
            boundaries = np.r_[0, np.flatnonzero(np.diff(sorted_linear)) + 1, len(order)]
            colors = np.full(shape, -1, dtype=np.int16)
            flat_colors = colors.ravel()
            for start, end in zip(boundaries[:-1], boundaries[1:], strict=True):
                values, counts = np.unique(point_codes[order[start:end]], return_counts=True)
                flat_colors[sorted_linear[start]] = values[np.argmax(counts)]

    return VoxelVolume(
        occupancy=occupancy,
        voxel_size_mm=tuple(float(value) for value in size),
        origin_mm=tuple(float(value) for value in origin),
        semantic="point-sampled",
        source=source,
        colors=colors,
    )


def voxel_to_mesh(volume: VoxelVolume) -> o3d.geometry.TriangleMesh:
    """使用 Marching Cubes 将占用网格转换为三角网格。"""
    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:
        raise RuntimeError("Voxel→Mesh 需要 scikit-image，请先安装项目依赖") from exc

    padded = np.pad(volume.occupancy.astype(np.float32), 1, mode="constant")
    vertices, faces, normals, _ = marching_cubes(
        padded,
        level=0.5,
        spacing=volume.voxel_size_mm,
        allow_degenerate=False,
    )
    offset = np.asarray(volume.origin_mm) - 0.5 * np.asarray(volume.voxel_size_mm)
    vertices += offset
    mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(vertices),
        o3d.utility.Vector3iVector(faces.astype(np.int32)),
    )
    mesh.vertex_normals = o3d.utility.Vector3dVector(normals)
    return cleanup_mesh(mesh)


def mesh_file_to_voxel_file(
    input_path: str | Path,
    output_path: str | Path,
    voxel_size_mm: tuple[float, float, float],
    fill: str,
    source_unit_mm: float = 1.0,
) -> VoxelVolume:
    mesh = read_mesh(input_path)
    if source_unit_mm <= 0:
        raise ValueError("source_unit_mm 必须大于 0")
    if source_unit_mm != 1.0:
        mesh.scale(source_unit_mm, center=(0.0, 0.0, 0.0))
    volume = mesh_to_voxel(
        mesh,
        voxel_size_mm=voxel_size_mm,
        fill=fill,
        source=str(Path(input_path).resolve()),
    )
    volume.save(output_path)
    return volume


def point_cloud_file_to_voxel_file(
    input_path: str | Path,
    output_path: str | Path,
    voxel_size_mm: tuple[float, float, float],
    source_unit_mm: float = 1.0,
) -> VoxelVolume:
    volume = point_cloud_to_voxel(
        input_path,
        voxel_size_mm=voxel_size_mm,
        source_unit_mm=source_unit_mm,
    )
    volume.save(output_path)
    return volume


def voxel_file_to_mesh_file(input_path: str | Path, output_path: str | Path) -> None:
    volume = VoxelVolume.load(input_path)
    write_mesh(output_path, voxel_to_mesh(volume))
