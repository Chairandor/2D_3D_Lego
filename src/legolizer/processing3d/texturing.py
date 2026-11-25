"""从彩色点云传递颜色，并生成可交换的 OBJ/MTL/PNG 与 GLB 纹理资产。"""

from __future__ import annotations

import math
from pathlib import Path
import zipfile

import numpy as np
import open3d as o3d
from PIL import Image
from scipy.ndimage import map_coordinates
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
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


def split_vertex_fans(mesh: o3d.geometry.TriangleMesh) -> o3d.geometry.TriangleMesh:
    """Split nonmanifold vertex fans without moving coordinates or removing faces."""
    faces = np.asarray(mesh.triangles)
    corners = np.arange(faces.size).reshape(faces.shape)
    edges = np.concatenate([faces[:, [i, j]] for i, j in ((0, 1), (1, 2), (2, 0))])
    endpoints = np.concatenate([corners[:, [i, j]] for i, j in ((0, 1), (1, 2), (2, 0))])
    order = np.argsort(edges, axis=1)
    edges = np.take_along_axis(edges, order, axis=1)
    endpoints = np.take_along_axis(endpoints, order, axis=1)
    _, groups, counts = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    if (counts > 2).any():
        raise ValueError("UV atlas requires an edge-manifold mesh")
    pairs = endpoints[np.argsort(groups, kind="stable")][np.repeat(counts == 2, counts)].reshape(-1, 2, 2)
    first, second = pairs[:, 0].ravel(), pairs[:, 1].ravel()
    graph = coo_matrix((np.ones(len(first)), (first, second)), shape=(faces.size, faces.size))
    _, mapping = connected_components(graph, directed=False)
    _, representative = np.unique(mapping, return_index=True)
    vertices = np.asarray(mesh.vertices)[faces.ravel()[representative]]
    result = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(vertices),
                                      o3d.utility.Vector3iVector(mapping.reshape(faces.shape)))
    if not result.is_vertex_manifold() or not result.orient_triangles():
        raise ValueError("Mesh remains unsuitable for UVAtlas after splitting vertex fans")
    return result.compute_vertex_normals()


def sample_texture(texture: np.ndarray, uvs: np.ndarray) -> np.ndarray:
    """Read actual PNG sRGB with bilinear filtering and OBJ's lower-left UV origin."""
    height, width = texture.shape[:2]
    rows = (1. - uvs[:, 1]) * height - .5
    cols = uvs[:, 0] * width - .5
    return np.column_stack([map_coordinates(texture[:, :, k].astype(np.float32), [rows, cols],
                                            order=1, mode="nearest") for k in range(3)]) / 255.


def closest(scene: o3d.t.geometry.RaycastingScene, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Obtain face IDs and three barycentric weights for closest mesh points."""
    result = scene.compute_closest_points(o3d.core.Tensor(points.astype(np.float32)), nthreads=4)
    weights = result["primitive_uvs"].numpy()
    return result["primitive_ids"].numpy(), np.column_stack((1. - weights.sum(axis=1), weights))


def export_indexed_obj(mesh: o3d.geometry.TriangleMesh, uvs: np.ndarray,
                       texture: np.ndarray, directory: Path) -> None:
    """Save textured OBJ with independent UV indices, PNG, and a Y-up preview GLB."""
    directory.mkdir()
    image = Image.fromarray(texture)
    image.save(directory / "mesh_texture.png")
    (directory / "mesh_textured.mtl").write_text("newmtl point_rgb\nKd 1 1 1\nmap_Kd mesh_texture.png\n", encoding="utf-8")
    faces, vertices, normals = map(np.asarray, (mesh.triangles, mesh.vertices, mesh.vertex_normals))
    with (directory / "mesh_textured.obj").open("w", encoding="utf-8") as out:
        out.write("# mm, Z-up\nmtllib mesh_textured.mtl\nusemtl point_rgb\n")
        np.savetxt(out, vertices, fmt="v %.9g %.9g %.9g")
        np.savetxt(out, normals, fmt="vn %.8g %.8g %.8g")
        np.savetxt(out, uvs.reshape(-1, 2), fmt="vt %.9g %.9g")
        for i, face in enumerate(faces + 1):
            out.write("f " + " ".join(f"{v}/{3*i+j+1}/{v}" for j, v in enumerate(face)) + "\n")
    preview = trimesh.Trimesh(vertices=vertices[faces].reshape(-1, 3),
                              faces=np.arange(faces.size).reshape(-1, 3),
                              vertex_normals=normals[faces].reshape(-1, 3), process=False)
    preview.visual = trimesh.visual.texture.TextureVisuals(uv=uvs.reshape(-1, 2),
        material=trimesh.visual.material.PBRMaterial(baseColorTexture=image, metallicFactor=0., roughnessFactor=.85))
    preview.apply_transform(Z_TO_Y)
    preview.export(directory / "mesh_textured.glb")
    with zipfile.ZipFile(directory / "textured_obj.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("mesh_textured.obj", "mesh_textured.mtl", "mesh_texture.png"):
            archive.write(directory / name, name)


def bake_uv_atlas(mesh: o3d.geometry.TriangleMesh, directory: Path, resolution: int = 1024) -> dict:
    """Build UVAtlas charts, bake transferred mesh RGB, and export the original PNG and UVs."""
    tensor = o3d.t.geometry.TriangleMesh.from_legacy(mesh)
    tensor.compute_uvatlas(resolution, gutter=4, parallel_partitions=1, nthreads=4)
    tensor.vertex["bake_rgb"] = tensor.vertex.colors
    baked = tensor.bake_vertex_attr_textures(resolution, {"bake_rgb"}, margin=2,
                                             fill=240. / 255., update_material=False)["bake_rgb"].numpy()
    export_indexed_obj(mesh, tensor.triangle.texture_uvs.numpy(),
                       np.rint(np.clip(baked, 0., 1.) * 255.).astype(np.uint8), directory)
    return uv_atlas_info(mesh, directory)


def uv_atlas_info(mesh: o3d.geometry.TriangleMesh, directory: Path) -> dict:
    """Describe a saved UVAtlas bundle and its unadjusted source texture."""
    with Image.open(directory / "mesh_texture.png") as atlas:
        size = list(atlas.size)
    return dict(method="uvatlas-point-rgb", triangles=len(mesh.triangles), atlas_size=size,
                obj="mesh_textured.obj", mtl="mesh_textured.mtl", texture="mesh_texture.png",
                glb="mesh_textured.glb", archive="textured_obj.zip",
                note="PNG 保留点云 RGB。LEGO 从这张贴图取色，再按曝光和砖色表量化。")
