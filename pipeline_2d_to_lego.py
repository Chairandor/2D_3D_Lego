"""2D image -> Hunyuan shape -> UniTEX colour -> orientation -> LEGO.

This repository-external orchestrator keeps Hunyuan3D, UniTEX, and
``legolization`` in separate environments and does not edit any upstream
checkout.  Expensive stages are resumable and review-gated.

Usage (from any terminal)::

    python pipeline_2d_to_lego.py your.png
    python pipeline_2d_to_lego.py your.png --accept-geometry
    python pipeline_2d_to_lego.py your.png --from-shape shape.glb

Outputs land in ``runs\\<name>\\`` by default:
  - ``input/foreground.png``                 accepted RGBA cutout
  - ``hunyuan/mesh_shape_review_clean.glb``  colourless geometry
  - ``unitex/mv_rgb.png``                    six-view colour review
  - ``unitex/textured_mesh.glb``             final coloured mesh
  - ``oriented/mesh_upright.glb``            corrected Y-up mesh
  - ``oriented/lego_input.ply``              legolization input
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from project_config import load_project_config, venv_executable

ROOT = Path(__file__).resolve().parent
load_project_config(ROOT)
LEGACY_IMAGE_PYTHON = Path(os.environ.get("LEGACY_IMAGE_PYTHON", str(venv_executable(ROOT / ".venv-hunyuan", "python"))))
HUNYUAN_PYTHON = Path(
    os.environ.get("HUNYUAN_PYTHON", str(venv_executable(ROOT / ".venv-hunyuan", "python")))
)
LEGO_ROOT = ROOT / "legolization"
LEGO_PYTHON = Path(os.environ.get("LEGO_PYTHON", str(venv_executable(LEGO_ROOT / ".venv", "python"))))
LEGO_CLI = Path(os.environ.get("LEGO_CLI", str(venv_executable(LEGO_ROOT / ".venv", "legolization"))))
LDRAW_DIR = ROOT / "LDrawLibrary" / "ldraw"
SEGMENTATION_ROOT = ROOT / "segmentation_lab"
FLORENCE_PYTHON = venv_executable(SEGMENTATION_ROOT / "florence_env", "python")
SAM2_PYTHON = venv_executable(SEGMENTATION_ROOT / "sam2_env", "python")
FLORENCE_MODEL = SEGMENTATION_ROOT / "florence_models" / "Florence-2-large-ft"
SAM2_CHECKPOINT = SEGMENTATION_ROOT / "sam2_models" / "sam2.1_hiera_small.pt"
DEFAULT_UNITEX_HOST = os.environ.get("UNITEX_HOST", "user@your-gpu-host")
DEFAULT_UNITEX_KEY = Path(
    os.environ.get("UNITEX_SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519_unitex"))
)
DEFAULT_UNITEX_ROOT = os.environ.get("UNITEX_REMOTE_ROOT", "/data/unitex-work")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hunyuan + UniTEX + orientation + legolization pipeline.",
    )
    parser.add_argument(
        "image",
        nargs="?",
        type=Path,
        help="input photo",
    )
    parser.add_argument(
        "--from-shape",
        type=Path,
        default=None,
        help="resume from an existing uncoloured Hunyuan-compatible mesh",
    )
    parser.add_argument(
        "--from-textured",
        "--from-glb",
        dest="from_textured",
        type=Path,
        default=None,
        help="resume from an existing UniTEX textured GLB",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="run directory (default: E:\\3D_TEST\\runs\\<name>-hunyuan-unitex)",
    )
    parser.add_argument("--hunyuan-steps", type=int, default=50)
    parser.add_argument("--hunyuan-seed", type=int, default=42)
    parser.add_argument("--hunyuan-octree", type=int, default=384)
    parser.add_argument("--hunyuan-cpu-offload", action="store_true")
    parser.add_argument("--unitex-steps", type=int, default=28)
    parser.add_argument("--unitex-seed", type=int, default=63)
    parser.add_argument(
        "--unitex-projection",
        choices=("ltm", "direct"),
        default="ltm",
        help="ltm fills unseen surfaces; direct is a faster diagnostic projection",
    )
    parser.add_argument("--unitex-host", default=DEFAULT_UNITEX_HOST)
    parser.add_argument("--unitex-key", type=Path, default=DEFAULT_UNITEX_KEY)
    parser.add_argument("--unitex-remote-root", default=DEFAULT_UNITEX_ROOT)
    parser.add_argument("--unitex-six-view", type=Path, default=None,
                        help="accepted replacement mv_rgb.png; projection only")
    parser.add_argument("--unitex-cache-run-dir", type=Path, default=None,
                        help="run directory that owns the original stage-1 remote cache")
    parser.add_argument(
        "--unitex-gpu",
        type=int,
        default=int(os.environ.get("UNITEX_GPU", "1")),
        help="physical GPU index on the remote UniTEX host (default 1)",
    )
    parser.add_argument(
        "--target-studs",
        type=int,
        default=24,
        help="LEGO model width in studs (default 24; smaller is faster)",
    )
    parser.add_argument(
        "--strategy",
        choices=("greedy", "fast", "bond", "luo", "auto"),
        default="fast",
        help="brick placement strategy for `legolization build` (default fast)",
    )
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="also run the full portable bundle (BOM + instructions); slower",
    )
    parser.add_argument(
        "--quality",
        choices=("fast", "balanced", "exhaustive", "direct"),
        default="fast",
        help="only used with --bundle (default fast ~2 min)",
    )
    parser.add_argument(
        "--skip-lego",
        action="store_true",
        help="deprecated compatibility flag; LEGO is already opt-in",
    )
    parser.add_argument(
        "--preprocess-only",
        action="store_true",
        help="write foreground, mask, review, and report; then stop",
    )
    parser.add_argument(
        "--accept-preprocess",
        action="store_true",
        help="continue past a mask flagged for review after inspecting input/review.png",
    )
    parser.add_argument(
        "--accept-geometry",
        action="store_true",
        help="continue to UniTEX after reviewing the Hunyuan geometry",
    )
    parser.add_argument(
        "--accept-unitex",
        action="store_true",
        help="project an accepted UniTEX six-view sheet onto the mesh",
    )
    parser.add_argument(
        "--preprocess-mode",
        choices=("auto", "legacy", "off"),
        default="auto",
        help="auto uses Florence-2 + SAM 2 subject extraction; legacy uses the old mask code; "
        "off treats the input as an already accepted cutout",
    )
    parser.add_argument(
        "--orient",
        choices=("auto", "keep"),
        default="auto",
        help="auto aligns the textured mesh while preserving source-image up/front",
    )
    parser.add_argument(
        "--colour",
        choices=("albedo", "lit"),
        default="lit",
        help="lit preserves UniTEX's already-delighted albedo (default); "
        "albedo applies the legacy illumination flattener",
    )
    parser.add_argument(
        "--lego-palette",
        choices=("common", "full", "off"),
        default="common",
        help="snap mesh colours to real brick RGB before voxelization "
        "(common: widely available System colours, default; "
        "full: every opaque LDConfig solid; off: leave reconstructed colours)",
    )
    parser.add_argument(
        "--colour-coherence", choices=("on", "off"), default="on",
        help="merge near-uniform palette regions (default: on; use off to preserve raw mapping)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        help="experimentally smooth geometry and clean colour regions; defaults to mesh-only review",
    )
    parser.add_argument(
        "--build-lego",
        action="store_true",
        help="explicit final approval to run legolization",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rerun stages even when their expected output already exists",
    )
    parser.add_argument("--stage", choices=("convert",), default=None, help=argparse.SUPPRESS)
    parser.add_argument("--glb", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--ply", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def require(path: Path, label: str) -> Path:
    if not path.exists():
        raise SystemExit(f"missing {label}: {path}")
    return path


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    printable = " ".join(cmd)
    print(f"\n>> {printable}", flush=True)
    merged = os.environ.copy()
    if env:
        merged.update(env)
    subprocess.run(cmd, cwd=cwd, env=merged, check=True)


def _kmeans(features, k: int, iters: int = 24, seed: int = 0):
    import numpy as np

    rng = np.random.default_rng(seed)
    count = len(features)
    k = max(1, min(int(k), count))
    centers = features[rng.choice(count, size=k, replace=False)].astype(np.float32)
    labels = np.zeros(count, dtype=np.int32)
    for _ in range(iters):
        dist = ((features[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = dist.argmin(axis=1).astype(np.int32)
        new_centers = centers.copy()
        for index in range(k):
            members = features[labels == index]
            if len(members):
                new_centers[index] = members.mean(axis=0)
        if np.allclose(new_centers, centers, atol=1e-5):
            break
        centers = new_centers
    return labels, centers


def flatten_illumination(mesh, n_materials: int = 6):
    """Replace baked shading with per-material albedo.

    Some reconstructed textures still contain photo lighting (grey shadows on a white
    body, etc.). LEGO sampling would otherwise turn those into extra grey
    bricks. Neutral pixels collapse to white/black; remaining hues are
    clustered and lifted to their bright (unshaded) colour.
    """
    import numpy as np
    from trimesh.visual.color import ColorVisuals

    colors = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
    if colors is None or len(colors) != len(mesh.vertices):
        print("illumination flatten skipped: no vertex colors", flush=True)
        return mesh

    rgb = np.asarray(colors)[:, :3].astype(np.float32) / 255.0
    lum = rgb.max(axis=1)
    chroma = rgb / np.clip(lum[:, None], 0.08, None)
    sat = 1.0 - rgb.min(axis=1) / np.clip(lum, 1e-6, None)
    black = lum < 0.14
    white = (sat < 0.28) & ~black
    chromatic = ~white & ~black

    flat = np.asarray(colors).copy()
    if white.any():
        peak = float(np.percentile(lum[white], 90))
        bright = white & (lum >= float(np.percentile(lum[white], 70)))
        albedo = chroma[bright].mean(axis=0) * max(peak, 0.85)
        flat[white, :3] = np.clip(np.round(np.clip(albedo, 0, 1) * 255.0), 0, 255)
    if black.any():
        flat[black, :3] = 0

    chroma_clusters = 0
    if chromatic.any():
        k = min(n_materials, int(chromatic.sum()))
        labels, _centers = _kmeans(chroma[chromatic], k)
        chroma_clusters = 0
        cluster_rgb = []
        cluster_verts = []
        for index in range(int(labels.max()) + 1):
            member = labels == index
            if not member.any():
                continue
            verts = np.flatnonzero(chromatic)[member]
            peak = float(np.percentile(lum[verts], 90))
            albedo = np.clip(chroma[verts].mean(axis=0) * max(peak, 0.45), 0.0, 1.0)
            cluster_rgb.append(albedo)
            cluster_verts.append(verts)
        merged = _merge_similar_colours(cluster_rgb, cluster_verts)
        chroma_clusters = len(merged)
        for albedo, verts in merged:
            flat[verts, :3] = np.clip(np.round(albedo * 255.0), 0, 255)

    if flat.shape[1] > 3:
        flat[:, 3] = 255
    mesh.visual = ColorVisuals(vertex_colors=flat.astype(np.uint8))
    unique = len(np.unique(flat[:, :3], axis=0))
    print(
        f"flattened lighting -> {unique} albedo colours "
        f"(white/black + {chroma_clusters} hues; shadows dropped)",
        flush=True,
    )
    return mesh


def _merge_similar_colours(rgbs, vert_groups, max_chroma_dist: float = 0.18):
    """Collapse hue clusters that are the same material under different lighting."""
    import numpy as np

    items = [
        [np.asarray(rgb, dtype=np.float32), np.asarray(verts)]
        for rgb, verts in zip(rgbs, vert_groups)
        if len(verts)
    ]
    while len(items) >= 2:
        chromas = [rgb / max(float(rgb.max()), 1e-6) for rgb, _ in items]
        best = None
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                dist = float(np.linalg.norm(chromas[i] - chromas[j]))
                if best is None or dist < best[0]:
                    best = (dist, i, j)
        if best is None or best[0] > max_chroma_dist:
            break
        _, i, j = best
        keep, drop = (i, j) if len(items[i][1]) >= len(items[j][1]) else (j, i)
        items[keep][1] = np.concatenate([items[keep][1], items[drop][1]])
        items.pop(drop)
    return [(rgb, verts) for rgb, verts in items]


# Solid colours that actually show up in bulk System parts. The full LDConfig
# set (~111 solids) includes ink / gold / fabuland / obsolete swatches that
# win nearest-neighbour against the bricks people expect (salmon comb →
# Fluorescent_Red_Ink instead of Red/Coral, mustard feet → Reddish_Gold
# instead of Medium Nougat).
COMMON_BRICK_CODES = (
    0,  # Black
    1,  # Blue
    2,  # Green
    4,  # Red
    14,  # Yellow
    15,  # White
    19,  # Tan
    25,  # Orange
    27,  # Lime
    28,  # Dark Tan
    70,  # Reddish Brown
    71,  # Light Bluish Grey
    72,  # Dark Bluish Grey
    73,  # Medium Blue
    84,  # Medium Nougat
    92,  # Nougat
    191,  # Bright Light Orange
    226,  # Bright Light Yellow
    272,  # Dark Blue
    288,  # Dark Green
    308,  # Dark Brown
    320,  # Dark Red
    321,  # Dark Azure
    322,  # Medium Azure
    353,  # Coral
    484,  # Dark Orange
)


def _brick_palette(mode: str):
    from legolization.color import Palette, default_palette
    import numpy as np

    pal = default_palette()
    if mode == "full":
        return pal
    keep = np.isin(pal.codes, np.asarray(COMMON_BRICK_CODES, dtype=np.int16))
    return Palette(
        codes=pal.codes[keep],
        rgbs=pal.rgbs[keep],
        names=tuple(name for name, flag in zip(pal.names, keep) if flag),
        oklabs=pal.oklabs[keep],
    )


def map_to_lego_colours(mesh, mode: str = "common"):
    """Replace vertex colours with the nearest real LDraw brick RGB.

    Reconstructed albedo (and photos) use colours that do not exist as bricks.
    Quantizing here — rather than only inside legolization — makes the
    PLY, preview, and MPD share the same brick colours, and a restricted
    System palette avoids obscure LDConfig matches.
    """
    import numpy as np
    from trimesh.visual.color import ColorVisuals

    if mode == "off":
        print("lego palette mapping skipped (--lego-palette off)", flush=True)
        return mesh

    colors = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
    if colors is None or len(colors) != len(mesh.vertices):
        print("lego palette mapping skipped: no vertex colors", flush=True)
        return mesh

    pal = _brick_palette(mode)
    rgb = np.asarray(colors)[:, :3]
    unique, inverse = np.unique(rgb, axis=0, return_inverse=True)
    codes = pal.quantize(unique.astype(np.float64))
    mapped = np.asarray([pal.rgb_of(int(code)) for code in codes], dtype=np.uint8)
    print(f"mapped {len(unique)} colours onto {mode} LDraw palette:", flush=True)
    for src, code in zip(unique, codes):
        dest = pal.rgb_of(int(code))
        print(
            f"  {tuple(int(c) for c in src)} -> {int(code)} "
            f"{pal.name_of(int(code))} {dest}",
            flush=True,
        )
    flat = np.asarray(colors).copy()
    flat[:, :3] = mapped[inverse]
    if flat.shape[1] > 3:
        flat[:, 3] = 255
    mesh.visual = ColorVisuals(vertex_colors=flat.astype(np.uint8))
    return mesh


def _cube_rotations():
    """Proper 90-degree rotations of the coordinate frame (det = +1)."""
    from itertools import permutations, product

    import numpy as np

    mats = []
    for perm in permutations(range(3)):
        for signs in product((-1.0, 1.0), repeat=3):
            matrix = np.zeros((3, 3))
            for row, axis in enumerate(perm):
                matrix[row, axis] = signs[row]
            if abs(float(np.linalg.det(matrix)) - 1.0) < 1e-6:
                mats.append(matrix)
    return mats


def _upright_score(mesh) -> float:
    """Prefer a compact ground contact without using object-specific colours."""
    import numpy as np

    vertices = np.asarray(mesh.vertices)
    ymin = float(vertices[:, 1].min())
    ymax = float(vertices[:, 1].max())
    span = max(ymax - ymin, 1e-6)
    bottom = vertices[:, 1] <= ymin + 0.08 * span
    support = vertices[bottom][:, [0, 2]]
    support_area = (
        float(np.ptp(support[:, 0]) * np.ptp(support[:, 1])) if bottom.any() else 9e9
    )
    com_h = (float(vertices[:, 1].mean()) - ymin) / span
    return 3.0 * com_h - 6.0 * support_area


def _input_view_score(rotation, face_rotation) -> float:
    """Keep the image's viewing side and screen-right after axis snapping.

    The current Hunyuan/UniTEX handoff preserves a Y-up mesh with the
    conditioning-camera side at -Z and image screen-right at -X.
    A 90-degree cube alignment otherwise has four equally upright yaw choices.
    """
    import numpy as np

    transform = rotation @ face_rotation
    front = (transform @ np.array([0.0, 0.0, -1.0]))[[0, 2]]
    right = (transform @ np.array([-1.0, 0.0, 0.0]))[[0, 2]]
    front /= max(float(np.linalg.norm(front)), 1e-9)
    right /= max(float(np.linalg.norm(right)), 1e-9)
    return float(front @ np.array([0.0, -1.0]) + 0.35 * right @ np.array([-1.0, 0.0]))


def _face_axis_rotation(mesh):
    """Rotation that maps dominant face normals onto XYZ (blocky meshes)."""
    import numpy as np

    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces, dtype=np.float64)
    remaining = np.ones(len(normals), dtype=bool)
    axes = []
    cosine = float(np.cos(np.radians(25.0)))
    for _ in range(3):
        idx = np.flatnonzero(remaining)
        if len(idx) == 0:
            break
        seed = idx[int(np.argmax(weights[idx]))]
        direction = normals[seed]
        clustered = remaining & (np.abs(normals @ direction) >= cosine)
        signs = np.sign(normals[clustered] @ direction)
        signs[signs == 0] = 1
        mean = (normals[clustered] * (weights[clustered] * signs)[:, None]).sum(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm < 1e-9:
            break
        axes.append(mean / norm)
        remaining &= ~clustered
    if len(axes) < 3:
        covariance = np.zeros((3, 3), dtype=np.float64)
        for normal, weight in zip(normals, weights):
            covariance += weight * np.outer(normal, normal)
        _vals, evecs = np.linalg.eigh(covariance)
        rotation = np.asarray(evecs, dtype=np.float64)
    else:
        rotation = np.column_stack(axes)
        q, _r = np.linalg.qr(rotation)
        rotation = q
    if float(np.linalg.det(rotation)) < 0:
        rotation[:, 0] *= -1
    transform = np.eye(4)
    transform[:3, :3] = rotation.T
    return transform


def _level_to_ground(mesh):
    """Kill leftover pitch/roll, then yaw-snap cube sides to world X/Z."""
    import numpy as np
    from trimesh.geometry import align_vectors
    from trimesh.transformations import rotation_matrix

    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces, dtype=np.float64)
    down = normals[:, 1] < -0.5
    if down.any():
        mean = (normals[down] * weights[down, None]).sum(axis=0)
        mean /= np.linalg.norm(mean)
        mesh.apply_transform(align_vectors(mean, np.array([0.0, -1.0, 0.0])))

    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces, dtype=np.float64)
    side = np.abs(normals[:, 1]) < 0.35
    if side.any():
        xz = normals[side][:, [0, 2]]
        angles = np.arctan2(xz[:, 1], xz[:, 0])
        folded = (angles + np.pi / 4.0) % (np.pi / 2.0) - np.pi / 4.0
        yaw = -float(np.average(folded, weights=weights[side]))
        if abs(yaw) > np.radians(0.5):
            mesh.apply_transform(rotation_matrix(yaw, [0.0, 1.0, 0.0]))
    return mesh


def _point_in_convex(poly, point) -> bool:
    import numpy as np

    if len(poly) < 3:
        return False
    try:
        from scipy.spatial import ConvexHull

        verts = poly[ConvexHull(poly).vertices]
    except Exception:  # noqa: BLE001
        verts = poly
    shifted = np.vstack([verts, verts[:1]])
    edge = shifted[1:] - shifted[:-1]
    to_pt = np.asarray(point, dtype=np.float64) - shifted[:-1]
    crosses = edge[:, 0] * to_pt[:, 1] - edge[:, 1] * to_pt[:, 0]
    return bool(np.all(crosses >= -1e-7) or np.all(crosses <= 1e-7))


def report_stance(mesh) -> None:
    """Log floor tilt and whether the COM sits over the feet (static stability)."""
    import numpy as np

    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces)
    nearest = np.abs(normals).max(axis=1)
    off = np.degrees(np.arccos(np.clip(nearest, 0.0, 1.0)))
    down = normals[:, 1] < -0.5
    tilt = 0.0
    if down.any():
        mean = (normals[down] * weights[down, None]).sum(axis=0)
        mean /= np.linalg.norm(mean)
        tilt = float(np.degrees(np.arccos(np.clip(-mean[1], -1.0, 1.0))))
    vertices = np.asarray(mesh.vertices)
    ymin = float(vertices[:, 1].min())
    span = max(float(np.ptp(vertices[:, 1])), 1e-6)
    bottom = vertices[:, 1] <= ymin + 0.05 * span
    support = vertices[bottom][:, [0, 2]]
    com = np.asarray(mesh.center_mass)
    stable = _point_in_convex(support, com[[0, 2]]) if bottom.any() else False
    print(
        f"stance: floor tilt {tilt:.1f}deg, "
        f"faces off-axis median {float(np.median(off)):.1f}deg, "
        f"COM over feet={stable}",
        flush=True,
    )


def upright_mesh(mesh):
    """Axis-align a single-image reconstruction without losing its image view.

    The reconstruction is Y-up and has a known conditioning-camera side. Those two
    source-frame directions disambiguate the 24 right-angle rotations after
    aligning broad faces to the brick grid. Colour cues are deliberately not
    used, so this also works for objects without chicken-like markings.
    """
    import numpy as np

    face_transform = _face_axis_rotation(mesh)
    mesh.apply_transform(face_transform)
    face_rotation = face_transform[:3, :3]
    best_mesh = None
    best_score = float("-inf")
    best_view_score = float("-inf")
    for rotation in _cube_rotations():
        transform = np.eye(4)
        transform[:3, :3] = rotation
        trial = mesh.copy()
        trial.apply_transform(transform)
        source_up = rotation @ face_rotation @ np.array([0.0, 1.0, 0.0])
        view_score = _input_view_score(rotation, face_rotation)
        # The source frame is the primary up cue. Geometry resolves
        # near-ties, and the camera cue fixes the remaining horizontal yaw.
        score = 100.0 * float(source_up[1]) + _upright_score(trial)
        score += 2.0 * view_score
        if score > best_score:
            best_score = score
            best_view_score = view_score
            best_mesh = trial
    assert best_mesh is not None
    best_mesh = _level_to_ground(best_mesh)
    ymin = float(best_mesh.bounds[0][1])
    best_mesh.apply_translation([0.0, -ymin, 0.0])
    print(
        f"upright mesh extents {np.round(best_mesh.extents, 4).tolist()} "
        f"(Y-up, seated on Y=0, score={best_score:.1f}, "
        f"input-view alignment={best_view_score:.2f})",
        flush=True,
    )
    report_stance(best_mesh)
    return best_mesh


def convert_glb_to_ply(
    glb_path: Path,
    ply_path: Path,
    *,
    orient: str = "auto",
    colour: str = "albedo",
    lego_palette: str = "common",
    colour_coherence: bool = True,
    refine: bool = False,
) -> None:
    """Bake a textured GLB into vertex colors and write a .ply mesh.

    UniTEX meshes are Y-up. legolization does not read .glb, so this
    is the glue format. Vertex colors let ``colour_mode=sampled`` pick brick
    colours from the reconstructed texture.
    """
    import numpy as np
    import trimesh

    loaded = trimesh.load(glb_path, force=None, process=False)
    if isinstance(loaded, trimesh.Scene):
        geoms = [
            geom
            for geom in loaded.geometry.values()
            if isinstance(geom, trimesh.Trimesh)
        ]
        if not geoms:
            raise SystemExit(f"no triangle meshes inside {glb_path}")
        mesh = trimesh.util.concatenate(geoms)
    elif isinstance(loaded, trimesh.Trimesh):
        mesh = loaded
    else:
        raise SystemExit(f"unsupported mesh type from {glb_path}: {type(loaded)}")

    visual = mesh.visual
    if visual is not None and hasattr(visual, "to_color"):
        try:
            mesh.visual = visual.to_color()
        except Exception as exc:  # noqa: BLE001 - baking is best-effort
            print(f"texture bake skipped: {exc}", flush=True)

    # Textured GLBs duplicate vertices along UV seams. Without welding, trimesh
    # reports hundreds of bodies and voxelization sees floating fragments.
    before_bodies = mesh.body_count
    mesh.merge_vertices()
    if before_bodies > 1:
        print(
            f"welded UV-seam vertices: bodies {before_bodies} -> {mesh.body_count}, "
            f"watertight={mesh.is_watertight}",
            flush=True,
        )

    if not mesh.is_watertight:
        try:
            trimesh.repair.fill_holes(mesh)
            trimesh.repair.fix_normals(mesh)
        except Exception as exc:  # noqa: BLE001 - networkx is optional
            print(f"hole fill skipped: {exc}", flush=True)

    if colour == "albedo":
        mesh = flatten_illumination(mesh)
    else:
        print("colour=lit, preserving UniTEX delighted colours", flush=True)
    protected = None
    if refine:
        from lego_refinement import recover_dark_image_details

        protected, detail_report = recover_dark_image_details(
            mesh, glb_path.with_name("input.png")
        )
        print(f"source detail recovery: {detail_report}", flush=True)
    source_colours = getattr(mesh.visual, "vertex_colors", None)
    source_rgb = (
        np.asarray(source_colours)[:, :3].copy()
        if source_colours is not None and len(source_colours) == len(mesh.vertices)
        else None
    )
    mesh = map_to_lego_colours(mesh, lego_palette)
    if colour_coherence and lego_palette != "off" and source_rgb is not None:
        from lego_colour_coherence import stabilize_palette_regions

        print(f"palette colour coherence: {stabilize_palette_regions(mesh, source_rgb)}", flush=True)
    if refine and lego_palette != "off":
        from lego_refinement import remove_tiny_colour_islands

        print(
            f"colour island cleanup: {remove_tiny_colour_islands(mesh, protected)}",
            flush=True,
        )

    if orient == "auto":
        before = [round(float(x), 4) for x in mesh.extents]
        mesh = upright_mesh(mesh)
        print(f"oriented tilted AABB {before} -> standing Y-up", flush=True)
    else:
        print("orient=keep, leaving source frame unchanged", flush=True)
    if refine and orient == "auto":
        from lego_refinement import flatten_shallow_markings, guided_normal_refine

        print(f"guided-normal geometry refinement: {guided_normal_refine(mesh)}", flush=True)
        print(f"flat source markings: {flatten_shallow_markings(mesh, protected)}", flush=True)

    ply_path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(ply_path)
    upright_glb = ply_path.with_name("mesh_upright.glb")
    mesh.export(upright_glb)
    colors = getattr(getattr(mesh, "visual", None), "vertex_colors", None)
    n_colors = 0 if colors is None else int(np.asarray(colors).shape[0])
    print(
        f"converted {glb_path.name} -> {ply_path.name} "
        f"({len(mesh.vertices)} verts, {len(mesh.faces)} faces, "
        f"{n_colors} vertex colors, watertight={mesh.is_watertight})",
        flush=True,
    )


def reconstruct_hunyuan(
    image: Path,
    output_dir: Path,
    *,
    steps: int,
    seed: int,
    octree_resolution: int,
    cpu_offload: bool,
) -> Path:
    """Generate only geometry through the repository-external Hunyuan runner."""
    require(HUNYUAN_PYTHON, "Hunyuan python")
    runner = require(ROOT / "run_hunyuan_shape_trial.py", "Hunyuan shape runner")
    output_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            str(HUNYUAN_PYTHON),
            str(runner),
            str(image),
            str(output_dir),
            "--steps",
            str(steps),
            "--seed",
            str(seed),
            "--octree-resolution",
            str(octree_resolution),
            *(["--cpu-offload"] if cpu_offload else []),
        ],
        cwd=ROOT,
        env={"PYTHONUNBUFFERED": "1"},
    )
    return require(output_dir / "mesh_shape_only.glb", "Hunyuan shape output")


def clean_and_review_shape(shape: Path, foreground: Path, output_dir: Path) -> Path:
    """Conservatively remove tiny detached bodies and produce geometry QA."""
    require(HUNYUAN_PYTHON, "Hunyuan python")
    run(
        [str(HUNYUAN_PYTHON), str(ROOT / "clean_shape_trial.py"), str(shape)],
        cwd=ROOT,
    )
    cleaned = require(shape.with_name("mesh_shape_review_clean.glb"), "cleaned shape")
    run(
        [
            str(HUNYUAN_PYTHON),
            str(ROOT / "shape_qa.py"),
            str(cleaned),
            str(foreground),
            str(output_dir / "qa"),
        ],
        cwd=ROOT,
    )
    return cleaned


def _ssh_base(key: Path) -> list[str]:
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def _scp_base(key: Path) -> list[str]:
    return [
        "scp",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def _remote_exec(host: str, key: Path, command: str) -> None:
    run([*_ssh_base(key), host, command])


def _remote_exists(host: str, key: Path, path: str) -> bool:
    result = subprocess.run(
        [*_ssh_base(key), host, f"test -f {shlex.quote(path)}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _file_digest(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _unitex_job_name(
    run_dir: Path,
    reference: Path,
    shape: Path,
    *,
    steps: int,
    seed: int,
    projection: str,
) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_dir.name).strip("-.") or "run"
    identity = "|".join(
        (_file_digest(reference), _file_digest(shape), str(steps), str(seed), projection)
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def run_unitex_remote(
    reference: Path,
    shape: Path,
    output_dir: Path,
    *,
    run_dir: Path,
    host: str,
    key: Path,
    remote_root: str,
    steps: int,
    seed: int,
    projection: str,
    gpu_index: int,
    phase: str,
    force: bool,
    six_view_override: Path | None = None,
    cache_run_dir: Path | None = None,
) -> Path | None:
    """Run a resumable UniTEX phase on the configured Linux GPU host."""
    execution = os.environ.get("UNITEX_EXECUTION", "ssh")
    if execution == "local":
        from unitex_tools.local_execution import execute
        return execute(reference, shape, output_dir, root=ROOT, remote_root=remote_root,
                       steps=steps, seed=seed, projection=projection, gpu_index=gpu_index,
                       phase=phase, force=force, run=run, six_view_override=six_view_override,
                       cache_run_dir=cache_run_dir)
    if execution != "ssh":
        raise ValueError("UNITEX_EXECUTION must be local or ssh")
    # Do not pre-open the private key from Python. On managed Windows hosts the
    # interpreter may be denied direct access to .ssh even though the approved
    # OpenSSH process can use the key normally. ssh/scp will report a precise
    # error if the configured identity is genuinely missing or unreadable.
    key = key.expanduser()
    wrapper = require(ROOT / "unitex_tools" / "run_unitex_external.py", "UniTEX runner")
    remote_root = remote_root.rstrip("/")
    job_name = _unitex_job_name(
        run_dir,
        reference,
        shape,
        steps=steps,
        seed=seed,
        projection=projection,
    )
    remote_job = f"{remote_root}/pipeline-runs/{job_name}"
    remote_output = f"{remote_job}/output"
    source_output = None
    if cache_run_dir is not None:
        source_job = _unitex_job_name(
            cache_run_dir, reference, shape, steps=steps, seed=seed, projection=projection
        )
        source_output = f"{remote_root}/pipeline-runs/{source_job}/output"
    remote_wrapper = f"{remote_job}/run_unitex_external.py"
    remote_reference = f"{remote_job}/reference.png"
    remote_shape = f"{remote_job}/shape.glb"
    remote_repo = f"{remote_root}/UniTEX"
    remote_python = f"{remote_root}/envs/unitex/bin/python"
    remote_flux = f"{remote_root}/models/FLUX.1-dev"
    remote_hf_home = f"{remote_root}/cache/huggingface"

    _remote_exec(host, key, f"mkdir -p {shlex.quote(remote_output)}")
    run([*_scp_base(key), str(wrapper), f"{host}:{remote_wrapper}"])
    run([*_scp_base(key), str(reference), f"{host}:{remote_reference}"])
    run([*_scp_base(key), str(shape), f"{host}:{remote_shape}"])

    def phase_command(requested_phase: str) -> str:
        return " ".join(
            [
                f"CUDA_VISIBLE_DEVICES={int(gpu_index)}",
                "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True",
                f"HF_HOME={shlex.quote(remote_hf_home)}",
                "HF_HUB_OFFLINE=1",
                "TRANSFORMERS_OFFLINE=1",
                "DIFFUSERS_OFFLINE=1",
                "UNITEX_RAY_BACKEND=open3d",
                f"UNITEX_FLUX_MODEL={shlex.quote(remote_flux)}",
                shlex.quote(remote_python),
                shlex.quote(remote_wrapper),
                "--repo",
                shlex.quote(remote_repo),
                "--image",
                shlex.quote(remote_reference),
                "--mesh",
                shlex.quote(remote_shape),
                "--output",
                shlex.quote(remote_output),
                "--phase",
                requested_phase,
                "--projection",
                projection,
                "--steps",
                str(steps),
                "--seed",
                str(seed),
                "--reference-mode",
                "rgba",
            ]
        )

    # A local review sheet alone cannot resume projection: camera tensors and
    # processed OBJ live in the remote cache. Recreate stage-1 only if that
    # cache has disappeared from the GPU host.
    remote_stage1_cache = f"{remote_output}/cache/camera_info.pth"
    if phase == "project" and not _remote_exists(host, key, remote_stage1_cache):
        source_cache = f"{source_output}/cache/camera_info.pth" if source_output else ""
        if source_output and _remote_exists(host, key, source_cache):
            _remote_exec(
                host, key,
                f"mkdir -p {shlex.quote(remote_output + '/cache')} && "
                f"cp -a {shlex.quote(source_output + '/cache/.')} {shlex.quote(remote_output + '/cache/')}",
            )
            print("reuse original UniTEX stage-1 cache in a separate branch", flush=True)
        else:
            print("remote UniTEX cache missing; rebuilding stage-1 before projection", flush=True)
            _remote_exec(host, key, phase_command("stage1"))

    if phase == "project" and six_view_override is not None:
        override = require(six_view_override.resolve(), "accepted UniTEX six-view image")
        if override.suffix.lower() != ".png":
            raise ValueError("UniTEX six-view replacement must be PNG")
        remote_sheet = f"{remote_output}/cache/mv_rgb.png"
        run([*_scp_base(key), str(override), f"{host}:{remote_sheet}"])
        _remote_exec(
            host, key,
            f"cp {shlex.quote(remote_sheet)} {shlex.quote(remote_output + '/mv_rgb.png')}",
        )

    marker = f"{remote_output}/mv_rgb.png" if phase == "stage1" else f"{remote_output}/textured_mesh.glb"
    if force or six_view_override is not None or not _remote_exists(host, key, marker):
        _remote_exec(host, key, phase_command(phase))
    else:
        print(f"reuse remote UniTEX {phase}: {marker}", flush=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    names = (
        ("rembg_image.png", "processed_image.png", "mv_alpha.png", "mv_normal.png", "mv_ccm.png", "mv_rgb_w_light.png", "mv_rgb.png")
        if phase == "stage1"
        else ("textured_mesh.glb", "completed_uv.png", "valid_uv_mask.png", "visable_uv_mask.png")
    )
    for name in names:
        remote_file = f"{remote_output}/{name}"
        if _remote_exists(host, key, remote_file):
            run([*_scp_base(key), f"{host}:{remote_file}", str(output_dir / name)])
    if phase == "stage1":
        return None
    return require(output_dir / "textured_mesh.glb", "UniTEX textured mesh")


def _next_segmentation_attempt(input_dir: Path) -> Path:
    attempts = input_dir / "segmentation-attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    index = 1
    while (attempts / f"attempt-{index:03d}").exists():
        index += 1
    target = attempts / f"attempt-{index:03d}"
    target.mkdir(parents=True)
    return target


def inspect_transparent_input(image: Path) -> dict[str, object]:
    """Decide whether an existing alpha channel is a usable subject cutout."""
    from PIL import Image

    with Image.open(image) as source:
        bands = source.getbands()
        if "A" not in bands:
            return {"accepted": False, "reason": "no alpha channel"}
        alpha = source.getchannel("A")
        histogram = alpha.histogram()
        pixels = max(1, alpha.width * alpha.height)
        transparent_fraction = sum(histogram[:16]) / pixels
        visible_fraction = sum(histogram[16:]) / pixels
        corners = (
            alpha.getpixel((0, 0)),
            alpha.getpixel((alpha.width - 1, 0)),
            alpha.getpixel((0, alpha.height - 1)),
            alpha.getpixel((alpha.width - 1, alpha.height - 1)),
        )
        transparent_corners = sum(value < 16 for value in corners)
        bbox = alpha.point(lambda value: 255 if value >= 16 else 0).getbbox()
    accepted = bool(
        bbox is not None
        and transparent_fraction >= 0.01
        and visible_fraction >= 0.005
        and transparent_corners >= 3
    )
    return {
        "accepted": accepted,
        "reason": "valid transparent cutout" if accepted else "alpha is not a clear transparent background",
        "transparent_fraction": round(transparent_fraction, 6),
        "visible_fraction": round(visible_fraction, 6),
        "transparent_corners": transparent_corners,
        "subject_bbox": list(bbox) if bbox is not None else None,
    }


def accept_transparent_input(
    image: Path,
    input_dir: Path,
    alpha_report: dict[str, object],
) -> tuple[Path, dict[str, object]]:
    """Standardize and record an already-transparent input without segmentation."""
    from PIL import Image, ImageDraw

    input_dir.mkdir(parents=True, exist_ok=True)
    source_digest = _file_digest(image)
    history = input_dir / "passthrough-history" / source_digest[:12]
    for name in ("foreground.png", "mask.png", "review.png", "preprocess.json"):
        old = input_dir / name
        if old.is_file():
            history.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, history / name)

    with Image.open(image) as source:
        rgba = source.convert("RGBA")
        alpha = rgba.getchannel("A")
        rgba.save(input_dir / "foreground.png")
        alpha.save(input_dir / "mask.png")
        tile = 24
        review = Image.new("RGB", rgba.size, (224, 224, 224))
        draw = ImageDraw.Draw(review)
        for y in range(0, rgba.height, tile):
            for x in range(0, rgba.width, tile):
                if (x // tile + y // tile) % 2:
                    draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=(184, 184, 184))
        review.paste(rgba, (0, 0), rgba)
        review.save(input_dir / "review.png")

    report: dict[str, object] = {
        "method": "transparent-input-passthrough",
        "source": str(image.resolve()),
        "source_sha1": source_digest,
        "foreground": str((input_dir / "foreground.png").resolve()),
        "needs_review": False,
        "segmentation_skipped": True,
        "alpha": alpha_report,
    }
    (input_dir / "preprocess.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("valid transparent input detected; Florence/SAM segmentation skipped", flush=True)
    return input_dir / "foreground.png", report


def prepare_foreground_auto(image: Path, input_dir: Path) -> tuple[Path, dict[str, object]]:
    alpha_report = inspect_transparent_input(image)
    if alpha_report["accepted"]:
        return accept_transparent_input(image, input_dir, alpha_report)
    return prepare_foreground_semantic(image, input_dir)


def prepare_foreground_semantic(image: Path, input_dir: Path) -> tuple[Path, dict[str, object]]:
    """Use Florence-2 for a semantic box and SAM 2 for the subject mask.

    The experimental pixel-level repair is deliberately not applied: the
    fixed-seed Hunyuan comparison showed that visually cleaner alpha edges can
    still create worse 3D protrusions. Every attempt is preserved for review.
    """
    for path, label in (
        (FLORENCE_PYTHON, "Florence python"),
        (SAM2_PYTHON, "SAM 2 python"),
        (FLORENCE_MODEL, "Florence model"),
        (SAM2_CHECKPOINT, "SAM 2 checkpoint"),
    ):
        require(path, label)
    attempt = _next_segmentation_attempt(input_dir)
    detection_dir = attempt / "florence"
    sam_dir = attempt / "sam2"
    selected_dir = attempt / "selected"
    run(
        [
            str(FLORENCE_PYTHON),
            str(SEGMENTATION_ROOT / "florence_detect.py"),
            str(image),
            "--model-dir",
            str(FLORENCE_MODEL),
            "--output-dir",
            str(detection_dir),
        ],
        cwd=SEGMENTATION_ROOT,
    )
    run(
        [
            str(SAM2_PYTHON),
            str(SEGMENTATION_ROOT / "sam2_from_detected_box.py"),
            "--detection-report",
            str(detection_dir / "report.json"),
            "--checkpoint",
            str(SAM2_CHECKPOINT),
            "--output-dir",
            str(sam_dir),
        ],
        cwd=SEGMENTATION_ROOT,
    )
    run(
        [
            str(SAM2_PYTHON),
            str(SEGMENTATION_ROOT / "select_sam2_candidate.py"),
            "--detection-report",
            str(detection_dir / "report.json"),
            "--sam-report",
            str(sam_dir / "report.json"),
            "--output-dir",
            str(selected_dir),
        ],
        cwd=SEGMENTATION_ROOT,
    )
    input_dir.mkdir(parents=True, exist_ok=True)
    previous = attempt / "previous-root"
    for name in ("foreground.png", "mask.png", "review.png", "preprocess.json"):
        old = input_dir / name
        if old.is_file():
            previous.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, previous / name)
    for source_name, target_name in (
        ("foreground.png", "foreground.png"),
        ("mask.png", "mask.png"),
        ("overlay.png", "review.png"),
    ):
        shutil.copy2(selected_dir / source_name, input_dir / target_name)
    selection = json.loads((selected_dir / "report.json").read_text(encoding="utf-8"))
    report: dict[str, object] = {
        "method": "florence-2-large-ft + sam2.1-hiera-small",
        "source": str(image.resolve()),
        "source_sha1": _file_digest(image),
        "attempt": str(attempt.resolve()),
        "foreground": str((input_dir / "foreground.png").resolve()),
        "needs_review": True,
        "selection": selection,
        "edge_repair": "disabled; prior fixed-seed Hunyuan ablation produced worse protrusions",
    }
    (input_dir / "preprocess.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return input_dir / "foreground.png", report


def prepare_foreground_legacy(image: Path, input_dir: Path) -> tuple[Path, dict[str, object]]:
    """Run the older mask fusion code for comparison or regression checks."""
    require(LEGACY_IMAGE_PYTHON, "legacy image-processing python")
    require(ROOT / "lego_preprocess.py", "LEGO foreground preprocessor")
    attempt = _next_segmentation_attempt(input_dir)
    legacy_output = attempt / "legacy"
    run(
        [
            str(LEGACY_IMAGE_PYTHON),
            str(ROOT / "lego_preprocess.py"),
            str(image),
            "--output-dir",
            str(legacy_output),
        ]
    )
    previous = attempt / "previous-root"
    for name in ("foreground.png", "mask.png", "review.png", "preprocess.json"):
        old = input_dir / name
        if old.is_file():
            previous.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, previous / name)
        shutil.copy2(legacy_output / name, input_dir / name)
    report = json.loads((input_dir / "preprocess.json").read_text(encoding="utf-8"))
    report["source_sha1"] = _file_digest(image)
    (input_dir / "preprocess.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return require(input_dir / "foreground.png", "foreground image"), report


def convert_via_lego_python(
    glb_path: Path,
    ply_path: Path,
    *,
    orient: str,
    colour: str,
    lego_palette: str,
    colour_coherence: bool,
    refine: bool,
) -> None:
    require(LEGO_PYTHON, "legolization venv python")
    run(
        [
            str(LEGO_PYTHON),
            str(Path(__file__).resolve()),
            "--stage",
            "convert",
            "--glb",
            str(glb_path),
            "--ply",
            str(ply_path),
            "--orient",
            orient,
            "--colour",
            colour,
            "--lego-palette",
            lego_palette,
            "--colour-coherence",
            "on" if colour_coherence else "off",
            *(["--refine"] if refine else []),
        ]
    )


def _lego_env() -> dict[str, str]:
    env = {"PYTHONUNBUFFERED": "1"}
    if LDRAW_DIR.is_dir():
        env["LDRAWDIR"] = str(LDRAW_DIR)
    return env


def _mesh_overrides(target_studs: int) -> list[str]:
    return [
        "--set",
        "input.mesh.up=y",
        "--set",
        f"input.mesh.target_studs={target_studs}",
        "--set",
        "input.mesh.colour_mode=sampled",
        "--set",
        "input.mesh.fill=false",
        "--set",
        "input.mesh.grid_phases=1",
        "--set",
        "geometry.hollow=false",
        "--set",
        "instructions.options.stability_policy=warn",
    ]


def run_legolization_build(
    ply_path: Path,
    model_path: Path,
    *,
    target_studs: int,
    strategy: str,
) -> None:
    lego_python = require(LEGO_PYTHON, "legolization Python")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(lego_python), "-m", "legolization.main",
        "build",
        str(ply_path),
        "-o",
        str(model_path),
        "--strategy",
        strategy,
        "--target-studs",
        str(target_studs),
        "--no-manifest",
        *_mesh_overrides(target_studs),
    ]
    run(cmd, cwd=LEGO_ROOT, env=_lego_env())


def run_legolization_bundle(
    ply_path: Path,
    bundle_dir: Path,
    *,
    target_studs: int,
    quality: str,
) -> None:
    lego_python = require(LEGO_PYTHON, "legolization Python")
    bundle_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(lego_python), "-m", "legolization.main",
        "bundle",
        str(ply_path),
        "-o",
        str(bundle_dir),
        "--fresh",
        "--quality",
        quality,
        "--render",
        "off",
        *_mesh_overrides(target_studs),
    ]
    run(cmd, cwd=LEGO_ROOT, env=_lego_env())


def summarize(
    run_dir: Path,
    *,
    model_path: Path | None = None,
    bundle_dir: Path | None = None,
) -> None:
    print("\n=== pipeline state ===", flush=True)
    print(f"run dir: {run_dir}", flush=True)
    for pattern in (
        "input/foreground.png",
        "input/review.png",
        "hunyuan/mesh_shape_only.glb",
        "hunyuan/mesh_shape_review_clean.glb",
        "hunyuan/qa/shape_qa_overlay.png",
        "unitex/mv_rgb.png",
        "unitex/textured_mesh.glb",
        "oriented/mesh_upright.glb",
        "oriented/lego_input.ply",
    ):
        path = run_dir / pattern
        if path.exists():
            print(f"  {path}  ({path.stat().st_size} bytes)", flush=True)
    for path in (
        model_path,
        bundle_dir / "model" / "model.mpd" if bundle_dir is not None else None,
        bundle_dir / "bundle.json" if bundle_dir is not None else None,
    ):
        if path is not None and path.exists():
            print(f"  {path}  ({path.stat().st_size} bytes)", flush=True)
    print("Review GLBs in Blender; open an approved MPD in LDView or BrickLink Studio.", flush=True)


def record_pipeline_state(run_dir: Path, stage: str, **items: object) -> None:
    path = run_dir / "pipeline_state.json"
    state: dict[str, object] = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    state["pipeline"] = "hunyuan-unitex-orient-legolization"
    state["stage"] = stage
    state.update({key: str(value) if isinstance(value, Path) else value for key, value in items.items()})
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.stage == "convert":
        if args.glb is None or args.ply is None:
            raise SystemExit("--stage convert needs --glb and --ply")
        convert_glb_to_ply(
            args.glb,
            args.ply,
            orient=args.orient,
            colour=args.colour,
            lego_palette=args.lego_palette,
            colour_coherence=args.colour_coherence == "on",
            refine=args.refine,
        )
        return 0

    if args.from_shape is not None and args.from_textured is not None:
        raise SystemExit("choose only one of --from-shape and --from-textured")
    if args.from_textured is None and args.image is None:
        raise SystemExit("an image is required unless --from-textured is used")
    if args.preprocess_only and args.image is None:
        raise SystemExit("--preprocess-only requires an image")

    image = require(args.image.expanduser().resolve(), "input image") if args.image else None
    from_shape = (
        require(args.from_shape.expanduser().resolve(), "input shape")
        if args.from_shape is not None
        else None
    )
    from_textured = (
        require(args.from_textured.expanduser().resolve(), "input textured mesh")
        if args.from_textured is not None
        else None
    )
    name = image.stem if image is not None else from_textured.stem

    run_dir = (args.output_dir or (ROOT / "runs" / f"{name}-hunyuan-unitex")).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    input_dir = run_dir / "input"
    hunyuan_dir = run_dir / "hunyuan"
    unitex_dir = run_dir / "unitex"
    six_view_override = require(args.unitex_six_view.resolve(), "replacement six-view image") if args.unitex_six_view else None
    cache_run_dir = args.unitex_cache_run_dir.resolve() if args.unitex_cache_run_dir else None
    if six_view_override is not None and cache_run_dir is None:
        raise ValueError("--unitex-six-view requires --unitex-cache-run-dir")
    oriented_dir = run_dir / "oriented"
    ply_path = oriented_dir / "lego_input.ply"
    lego_name = f"lego-{args.target_studs}"
    model_path = run_dir / lego_name / "model.mpd"
    bundle_dir = run_dir / f"{lego_name}-bundle"

    foreground: Path | None = None
    shape: Path | None = None
    textured = from_textured

    if textured is None:
        assert image is not None
        if args.preprocess_mode in ("auto", "legacy"):
            print(f"[0/5] prepare foreground from {image.name}", flush=True)
            foreground_path = input_dir / "foreground.png"
            report_path = input_dir / "preprocess.json"
            if foreground_path.is_file() and report_path.is_file() and not args.force:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                method = str(report.get("method", ""))
                same_source = report.get("source_sha1") == _file_digest(image)
                compatible = (
                    method.startswith(("florence-2", "transparent-input"))
                    if args.preprocess_mode == "auto"
                    else not method.startswith(("florence-2", "transparent-input"))
                ) and same_source
                if compatible:
                    foreground = foreground_path
                    print(f"reuse foreground: {foreground}", flush=True)
                else:
                    print(
                        f"existing foreground method '{method}' does not match "
                        f"--preprocess-mode {args.preprocess_mode}; creating a preserved new attempt",
                        flush=True,
                    )
                    foreground, report = (
                        prepare_foreground_auto(image, input_dir)
                        if args.preprocess_mode == "auto"
                        else prepare_foreground_legacy(image, input_dir)
                    )
            else:
                foreground, report = (
                    prepare_foreground_auto(image, input_dir)
                    if args.preprocess_mode == "auto"
                    else prepare_foreground_legacy(image, input_dir)
                )
            if args.preprocess_only:
                record_pipeline_state(run_dir, "foreground_review", foreground=foreground)
                print(f"Foreground ready: {foreground}", flush=True)
                print(f"Review image: {input_dir / 'review.png'}", flush=True)
                return 0
            if report.get("needs_review") and not args.accept_preprocess:
                record_pipeline_state(run_dir, "foreground_review", foreground=foreground)
                print(
                    "Foreground extraction needs review; inspect input/review.png "
                    "and rerun with --accept-preprocess to continue.",
                    flush=True,
                )
                return 2
        else:
            foreground = image
            print(f"[0/5] foreground processing disabled; using {foreground}", flush=True)
            if args.preprocess_only:
                raise SystemExit("--preprocess-only requires --preprocess-mode auto or legacy")

        if from_shape is not None:
            shape = from_shape
            print(f"[1/5] reuse supplied Hunyuan shape: {shape}", flush=True)
        else:
            raw_shape = hunyuan_dir / "mesh_shape_only.glb"
            if raw_shape.is_file() and not args.force:
                print(f"[1/5] reuse Hunyuan shape: {raw_shape}", flush=True)
            else:
                print(f"[1/5] Hunyuan colourless geometry", flush=True)
                raw_shape = reconstruct_hunyuan(
                    foreground,
                    hunyuan_dir,
                    steps=args.hunyuan_steps,
                    seed=args.hunyuan_seed,
                    octree_resolution=args.hunyuan_octree,
                    cpu_offload=args.hunyuan_cpu_offload,
                )
            cleaned = raw_shape.with_name("mesh_shape_review_clean.glb")
            qa_report = hunyuan_dir / "qa" / "shape_qa.json"
            if args.force or not cleaned.is_file() or not qa_report.is_file():
                shape = clean_and_review_shape(raw_shape, foreground, hunyuan_dir)
            else:
                shape = cleaned
                print(f"reuse cleaned geometry and QA: {shape}", flush=True)

        if not args.accept_geometry:
            record_pipeline_state(run_dir, "geometry_review", foreground=foreground, shape=shape)
            print(
                f"Geometry review gate: inspect {shape}"
                + (f" and {hunyuan_dir / 'qa'}" if from_shape is None else "")
                + ", then rerun with --accept-geometry.",
                flush=True,
            )
            summarize(run_dir)
            return 2

        stage1 = unitex_dir / "mv_rgb.png"
        if six_view_override is not None:
            unitex_dir.mkdir(parents=True, exist_ok=True)
            if not stage1.is_file() or _file_digest(stage1) != _file_digest(six_view_override):
                shutil.copy2(six_view_override, stage1)
            if cache_run_dir is not None:
                original_alpha = cache_run_dir / "unitex" / "mv_alpha.png"
                branch_alpha = unitex_dir / "mv_alpha.png"
                if original_alpha.is_file() and not branch_alpha.is_file():
                    shutil.copy2(original_alpha, branch_alpha)
            print(f"[2/5] accepted replacement UniTEX six-view sheet: {stage1}", flush=True)
        elif args.force or not stage1.is_file():
            print("[2/5] UniTEX six-view colouring", flush=True)
            run_unitex_remote(
                foreground,
                shape,
                unitex_dir,
                run_dir=run_dir,
                host=args.unitex_host,
                key=args.unitex_key,
                remote_root=args.unitex_remote_root,
                steps=args.unitex_steps,
                seed=args.unitex_seed,
                projection=args.unitex_projection,
                gpu_index=args.unitex_gpu,
                phase="stage1",
                force=args.force,
            )
        else:
            print(f"[2/5] reuse UniTEX six-view sheet: {stage1}", flush=True)

        if not args.accept_unitex:
            record_pipeline_state(run_dir, "unitex_review", foreground=foreground, shape=shape)
            print(
                "UniTEX review gate: inspect unitex/mv_rgb.png, then rerun with "
                "--accept-geometry --accept-unitex.",
                flush=True,
            )
            summarize(run_dir)
            return 2

        textured_path = unitex_dir / "textured_mesh.glb"
        if args.force or not textured_path.is_file():
            print(f"[3/5] UniTEX {args.unitex_projection} projection", flush=True)
            textured = run_unitex_remote(
                foreground,
                shape,
                unitex_dir,
                run_dir=run_dir,
                host=args.unitex_host,
                key=args.unitex_key,
                remote_root=args.unitex_remote_root,
                steps=args.unitex_steps,
                seed=args.unitex_seed,
                projection=args.unitex_projection,
                gpu_index=args.unitex_gpu,
                phase="project",
                force=args.force,
                six_view_override=six_view_override,
                cache_run_dir=cache_run_dir,
            )
        else:
            textured = textured_path
            print(f"[3/5] reuse textured mesh: {textured}", flush=True)
    else:
        print(f"[0-3/5] resume from supplied textured mesh: {textured}", flush=True)

    assert textured is not None
    print(f"[4/5] colour bake and generic orientation", flush=True)
    convert_via_lego_python(
        textured,
        ply_path,
        orient=args.orient,
        colour=args.colour,
        lego_palette=args.lego_palette,
        colour_coherence=args.colour_coherence == "on",
        refine=args.refine,
    )

    if not args.build_lego or args.skip_lego:
        record_pipeline_state(run_dir, "oriented_review", textured=textured, lego_input=ply_path)
        print("[5/5] legolization is approval-gated; no bricks were built", flush=True)
        print("After reviewing oriented/mesh_upright.glb, rerun with --build-lego.", flush=True)
        summarize(run_dir, model_path=model_path, bundle_dir=bundle_dir)
        return 0

    print(
        f"[5/5] legolization build strategy={args.strategy} studs={args.target_studs}",
        flush=True,
    )
    run_legolization_build(
        ply_path,
        model_path,
        target_studs=args.target_studs,
        strategy=args.strategy,
    )
    if args.bundle:
        print(f"[5b] legolization bundle quality={args.quality}", flush=True)
        try:
            run_legolization_bundle(
                ply_path,
                bundle_dir,
                target_studs=args.target_studs,
                quality=args.quality,
            )
        except subprocess.CalledProcessError as exc:
            print(
                f"bundle finished with exit {exc.returncode} "
                "(partial is OK if model.mpd already exists)",
                flush=True,
            )
    record_pipeline_state(
        run_dir,
        "complete",
        textured=textured,
        lego_input=ply_path,
        lego_model=model_path,
        target_studs=args.target_studs,
    )
    summarize(run_dir, model_path=model_path, bundle_dir=bundle_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
