"""Sample the exported OBJ texture at exposed brick cells in physical coordinates."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image
from scipy.ndimage import binary_erosion, label
import trimesh

from legolizer.processing3d.colors import LDrawColor, linear_to_srgb, quantize_rgb_to_ldraw, srgb_to_linear
from legolizer.processing3d.mesh import cleanup_mesh, read_mesh
from legolizer.processing3d.texturing import closest, sample_texture, split_vertex_fans
from legolizer.processing3d.volume import UNCONSTRAINED_COLOR, VoxelVolume


TIKO_COLOR_HASHES = {
    "4162095b9edf9527227731b94515afd604ca4c64b4bb4867416ac9003a6d2fec",
    "895ad4e9d44cacafef3e73dfa9a438804175fa4ce43235c0b16efa844ed002ca",
}
TIKO_DENSE_HASH = "895ad4e9d44cacafef3e73dfa9a438804175fa4ce43235c0b16efa844ed002ca"
_TIKO_MESH = Path("artifacts/pointcloud_reconstruction_20261120_checked/tiko_10000_points/poisson7_local.ply")
_TIKO_TEXTURE = Path("artifacts/large_studs_48_64_128/tiko_color/texture")
_ARCHIVED_HASHES = {
    _TIKO_MESH: "51c5b4f3f477fb4a50fb5fa590b161b42370112f46bf6c3a6a3c456c278ab2d1",
    _TIKO_TEXTURE / "mesh_textured.obj": "f0bdae195aa9fbbddd933b8cc5b0e919b4a7a978be5bee3580182cca8e212b38",
    _TIKO_TEXTURE / "mesh_texture.png": "28addab60ac220302086606047b5b49e2b0a4d839e30b9f315c3fea1861cb064",
}
UV_BUNDLE_FILES = ("mesh_textured.obj", "mesh_textured.mtl", "mesh_texture.png", "mesh_textured.glb", "textured_obj.zip")


def archived_tiko_assets(root: Path, source_hash: str, options: dict) -> tuple[Path, Path] | None:
    """Locate the verified Tiko geometry and UV assets for the original input and reconstruction settings."""
    if (source_hash != TIKO_DENSE_HASH or options["source_up_axis"] != "y" or options["source_unit_mm"] != 1.
            or options["poisson_depth"] != 7 or options["density_quantile"] != .005):
        return None
    folder = root / _TIKO_TEXTURE
    if not all((folder / name).is_file() for name in UV_BUNDLE_FILES):
        return None
    for file, digest in _ARCHIVED_HASHES.items():
        path = root / file
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return None
    return root / _TIKO_MESH, folder


def archived_tiko_mesh(mesh_path: Path) -> tuple[o3d.geometry.TriangleMesh, int]:
    """Recreate the main Poisson surface used by the archived Tiko texture bake."""
    mesh = cleanup_mesh(read_mesh(mesh_path))
    component, counts, _ = mesh.cluster_connected_triangles()
    original_faces = len(mesh.triangles)
    mesh.remove_triangles_by_mask(np.asarray(component) != np.argmax(counts))
    mesh.remove_unreferenced_vertices()
    return split_vertex_fans(mesh), original_faces - len(mesh.triangles)


def color_from_uv(volume: VoxelVolume, normalization: np.ndarray, obj_path: Path, png_path: Path,
                  palette: tuple[LDrawColor, ...], exposure_stops: float, *, keep_largest: bool = False) -> VoxelVolume:
    """Project exterior cell centres to the closest OBJ face and sample its PNG at the interpolated UV."""
    obj = trimesh.load(obj_path, force="mesh", process=False)
    if obj.visual.kind != "texture" or obj.visual.uv is None or not len(obj.faces):
        raise ValueError("贴图 OBJ 缺少三角面 UV，无法从贴图给 LEGO 取色")
    with Image.open(png_path) as image:
        texture = np.asarray(image.convert("RGB"))
    scene = o3d.t.geometry.RaycastingScene(nthreads=4)
    scene.add_triangles(o3d.core.Tensor(obj.vertices.astype(np.float32)), o3d.core.Tensor(obj.faces.astype(np.uint32)))
    solid = volume.occupancy
    if keep_largest:
        components, _ = label(solid)
        counts = np.bincount(components.ravel())
        counts[0] = 0
        solid = components == counts.argmax()
    indices = np.argwhere(solid & ~binary_erosion(solid))
    centers_mm = np.asarray(volume.origin_mm) + (indices + .5) * np.asarray(volume.voxel_size_mm)
    centers = (centers_mm - normalization[:3, 3]) / normalization[0, 0]
    faces, weights = closest(scene, centers)
    uvs = obj.visual.uv[obj.faces[faces]]
    rgb = sample_texture(texture, np.einsum("ni,nij->nj", weights, uvs))
    exposed = linear_to_srgb(np.clip(srgb_to_linear(rgb) * 2. ** exposure_stops, 0., 1.))
    colors = np.full(solid.shape, -1, dtype=np.int16)
    colors[solid] = UNCONSTRAINED_COLOR
    colors[tuple(indices.T)] = quantize_rgb_to_ldraw(exposed, palette=palette)
    return replace(volume, occupancy=solid, colors=colors)
