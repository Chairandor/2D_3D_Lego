"""从彩色点云传递颜色，并生成可交换的 OBJ/MTL/PNG 与 GLB 纹理资产。"""

from __future__ import annotations

import math
from pathlib import Path
import zipfile

import numpy as np
import open3d as o3d
from PIL import Image
from scipy.spatial import cKDTree
import trimesh


Z_TO_Y = np.array([[1., 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]])


def transfer_point_colors(mesh: o3d.geometry.TriangleMesh,
                          cloud: o3d.geometry.PointCloud) -> dict:
    """在同一坐标/单位下用邻点反距离权重给 Mesh 顶点赋 RGB。"""
    if not cloud.has_colors():
        raise ValueError("彩色 PLY 路径需要 red/green/blue 字段")
    points, colors = np.asarray(cloud.points), np.asarray(cloud.colors)
    if not np.isfinite(colors).all():
        raise ValueError("点云颜色含非有限值")
    distances, indices = cKDTree(points).query(np.asarray(mesh.vertices), k=min(4, len(points)))
    if distances.ndim == 1:
        distances, indices = distances[:, None], indices[:, None]
    weights = 1.0 / np.maximum(distances, max(float(np.ptp(points, axis=0).max()) * 1e-9, 1e-12)) ** 2
    weights /= weights.sum(axis=1, keepdims=True)
    rgb = np.clip((colors[indices] * weights[:, :, None]).sum(axis=1), 0, 1)
    mesh.vertex_colors = o3d.utility.Vector3dVector(rgb)
    return dict(method="point-rgb-inverse-distance", neighbors=weights.shape[1],
                mean_nearest_distance=float(distances[:, 0].mean()),
                max_nearest_distance=float(distances[:, 0].max()),
                vertex_count=len(rgb))


def export_preview_glb(mesh: o3d.geometry.TriangleMesh, path: Path) -> Path:
    """把毫米 Z-up 网格转换到 glTF 的 Y-up，保留几何和顶点色。"""
    colors = np.rint(np.asarray(mesh.vertex_colors) * 255).astype(np.uint8) if mesh.has_vertex_colors() else None
    view = trimesh.Trimesh(vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.triangles),
                           vertex_colors=colors, process=False)
    view.apply_transform(Z_TO_Y)
    path.parent.mkdir(parents=True, exist_ok=True)
    view.export(path)
    return path


def export_texture_bundle(mesh: o3d.geometry.TriangleMesh, directory: Path,
                          tile_size: int = 8) -> dict:
    """给每个三角形独立 UV 小块，用顶点 RGB 插值烘焙贴图；输入毫米 Z-up。

    这是点云 RGB 的纹理化表达，不恢复输入里不存在的照片细节。
    OBJ 保持 Z-up，预览 GLB 显式转换 Y-up。
    """
    if not mesh.has_vertex_colors():
        raise ValueError("纹理生成需要已传递的顶点颜色")
    if tile_size < 4 or tile_size > 32:
        raise ValueError("tile_size 必须为 4–32")
    directory.mkdir(parents=True, exist_ok=True)
    faces = np.asarray(mesh.triangles)
    vertices = np.asarray(mesh.vertices)[faces].reshape(-1, 3)
    colors = np.asarray(mesh.vertex_colors)[faces]
    columns = math.ceil(math.sqrt(len(faces)))
    rows = math.ceil(len(faces) / columns)
    width, height = columns * tile_size, rows * tile_size
    if max(width, height) > 8192:
        raise ValueError("纹理图集超过 8192 像素，请降低 Poisson 深度或先简化 Mesh")
    atlas = np.full((height, width, 3), 240, dtype=np.uint8)
    uv = np.empty((len(faces), 3, 2), dtype=float)
    yy, xx = np.mgrid[:tile_size, :tile_size]
    u = np.clip((xx - 1) / (tile_size - 3), 0, 1)
    v = np.clip((yy - 1) / (tile_size - 3), 0, 1)
    bary = np.stack((np.maximum(1 - u - v, 0), u, v), axis=-1)
    bary /= bary.sum(axis=-1, keepdims=True)
    for i, rgb in enumerate(colors):
        x, y = (i % columns) * tile_size, (i // columns) * tile_size
        atlas[y:y + tile_size, x:x + tile_size] = np.rint(bary @ rgb * 255).astype(np.uint8)
        px = np.array([[x + 1.5, y + 1.5], [x + tile_size - 1.5, y + 1.5],
                       [x + 1.5, y + tile_size - 1.5]])
        uv[i, :, 0] = px[:, 0] / width
        uv[i, :, 1] = 1 - px[:, 1] / height
    image = Image.fromarray(atlas)
    image.save(directory / "mesh_texture.png")
    (directory / "mesh_textured.mtl").write_text(
        "newmtl point_rgb\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\nd 1\nmap_Kd mesh_texture.png\n", encoding="utf-8")
    with (directory / "mesh_textured.obj").open("w", encoding="utf-8") as stream:
        stream.write("# point RGB atlas; units mm; Z-up\nmtllib mesh_textured.mtl\nusemtl point_rgb\n")
        for vertex in vertices:
            stream.write("v " + " ".join(f"{v:.9g}" for v in vertex) + "\n")
        for coord in uv.reshape(-1, 2):
            stream.write(f"vt {coord[0]:.9g} {coord[1]:.9g}\n")
        for i in range(len(faces)):
            indices = [i * 3 + j + 1 for j in range(3)]
            stream.write("f " + " ".join(f"{j}/{j}" for j in indices) + "\n")
    textured = trimesh.Trimesh(vertices=vertices, faces=np.arange(len(vertices)).reshape(-1, 3), process=False)
    textured.visual = trimesh.visual.texture.TextureVisuals(uv=uv.reshape(-1, 2), image=image)
    textured.apply_transform(Z_TO_Y)
    textured.export(directory / "mesh_textured.glb")
    archive = directory / "textured_obj.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ("mesh_textured.obj", "mesh_textured.mtl", "mesh_texture.png"):
            bundle.write(directory / name, name)
    return dict(method="per-triangle-uv-vertex-rgb-bake", triangles=len(faces),
                atlas_size=[width, height], tile_size=tile_size,
                obj="mesh_textured.obj", mtl="mesh_textured.mtl", texture="mesh_texture.png",
                glb="mesh_textured.glb", archive="textured_obj.zip",
                note="由点云 RGB 传递并烘焙；不包含输入中不存在的照片纹理细节")
