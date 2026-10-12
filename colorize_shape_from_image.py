"""Project source-image albedo onto a shape-only mesh for LEGO sampling.

This is object-agnostic: the camera comes from silhouette alignment, visible
vertices sample the source, and hidden vertices inherit colour from the nearest
visible surface. It does not modify any upstream model repository.
"""

from __future__ import annotations

import argparse
import colorsys
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, distance_transform_edt, label
from scipy.spatial import cKDTree
import trimesh
from trimesh.visual.color import ColorVisuals

from pipeline_2d_to_lego import COMMON_BRICK_CODES, flatten_illumination
from render_refinement_comparison import render


def _srgb_to_oklab(rgb: np.ndarray) -> np.ndarray:
    srgb = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    lms = linear @ np.asarray([
        [0.4122214708, 0.2119034982, 0.0883024619],
        [0.5363325363, 0.6806995451, 0.2817188376],
        [0.0514459929, 0.1073969566, 0.6299787005],
    ])
    return np.cbrt(lms) @ np.asarray([
        [0.2104542553, 1.9779984951, 0.0259040371],
        [0.7936177850, -2.4285922050, 0.7827717662],
        [-0.0040720468, 0.4505937099, -0.8086757660],
    ])


def map_common_lego_colours(mesh: trimesh.Trimesh, ldconfig: Path) -> dict:
    pattern = re.compile(r"!COLOUR\s+(.*?)\s+CODE\s+(\d+)\s+VALUE\s+#([0-9A-Fa-f]{6})")
    allowed = set(COMMON_BRICK_CODES)
    entries = []
    for line in ldconfig.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if match and int(match.group(2)) in allowed:
            entries.append((int(match.group(2)), match.group(1), match.group(3)))
    if len(entries) != len(allowed):
        raise ValueError(f"common palette incomplete: {len(entries)}/{len(allowed)}")
    palette_rgb = np.asarray([
        [int(value[index:index + 2], 16) for index in (0, 2, 4)]
        for _code, _name, value in entries
    ], dtype=np.uint8)
    palette_lab = _srgb_to_oklab(palette_rgb)
    colours = np.asarray(mesh.visual.vertex_colors).copy()
    unique, inverse = np.unique(colours[:, :3], axis=0, return_inverse=True)
    source_lab = _srgb_to_oklab(unique)
    delta = source_lab[:, None, :] - palette_lab[None, :, :]
    delta[:, :, 1:] *= 3.0
    nearest = np.sum(delta * delta, axis=2).argmin(axis=1)
    colours[:, :3] = palette_rgb[nearest][inverse]
    colours[:, 3] = 255
    mesh.visual = ColorVisuals(vertex_colors=colours)
    return {
        "mapping": [
            {
                "source_rgb": src.tolist(),
                "code": entries[index][0],
                "name": entries[index][1],
                "rgb": palette_rgb[index].tolist(),
            }
            for src, index in zip(unique, nearest)
        ]
    }


def normalized_rgba(image: Image.Image, size: int = 1024) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"))
    mask = rgba[:, :, 3] >= 128
    rows, cols = np.nonzero(mask)
    if not len(rows):
        raise ValueError("empty source alpha")
    crop = Image.fromarray(
        rgba[rows.min():rows.max() + 1, cols.min():cols.max() + 1], "RGBA"
    )
    scale = 0.82 * size / max(crop.height, crop.width)
    target = (
        max(1, int(round(crop.width * scale))),
        max(1, int(round(crop.height * scale))),
    )
    crop = crop.resize(target, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((size - crop.width) // 2, (size - crop.height) // 2))
    return np.asarray(canvas)


def camera_direction(yaw_degrees: float, elevation_degrees: float) -> np.ndarray:
    yaw = np.deg2rad(yaw_degrees)
    elevation = np.deg2rad(elevation_degrees)
    return np.array([
        np.sin(yaw) * np.cos(elevation),
        np.sin(elevation),
        np.cos(yaw) * np.cos(elevation),
    ])


def project_pixels(mesh: trimesh.Trimesh, direction: np.ndarray, size: int) -> np.ndarray:
    centre = np.asarray(mesh.bounds).mean(axis=0)
    forward = -direction
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    points = np.asarray(mesh.vertices) - centre
    xy = np.column_stack((points @ right, points @ up))
    span = np.ptp(xy, axis=0)
    scale = 0.82 * size / max(float(span.max()), 1e-12)
    return np.column_stack((size / 2 + xy[:, 0] * scale, size / 2 - xy[:, 1] * scale))


def enclosed_dark_markings(source_rgba: np.ndarray) -> tuple[np.ndarray, int]:
    """Find small dark markings safely inside the subject, not cast shadows."""
    alpha = source_rgba[:, :, 3] >= 96
    dark = (source_rgba[:, :, :3].max(axis=2) < 85) & alpha
    labels, count = label(dark)
    boundary_distance = distance_transform_edt(alpha)
    foreground_area = max(1, int(alpha.sum()))
    protected = np.zeros_like(alpha)
    accepted = 0
    for index in range(1, count + 1):
        component = labels == index
        area = int(component.sum())
        if area < 8 or area > foreground_area * 0.02:
            continue
        # Enclosed graphic details are deep inside the matte; a cast shadow or
        # dirty cutout fringe stays near its outer boundary.
        if float(np.median(boundary_distance[component])) < 0.02 * alpha.shape[0]:
            continue
        protected |= component
        accepted += 1
    protected = binary_dilation(protected, iterations=max(2, alpha.shape[0] // 256))
    return protected, accepted


def projected_vertex_colours(
    mesh: trimesh.Trimesh,
    source_rgba: np.ndarray,
    yaw_degrees: float,
    elevation_degrees: float,
) -> tuple[np.ndarray, dict[str, int]]:
    size = source_rgba.shape[0]
    direction = camera_direction(yaw_degrees, elevation_degrees)
    visible_faces = np.asarray(mesh.face_normals) @ direction > 0
    visible_vertices = np.zeros(len(mesh.vertices), dtype=bool)
    visible_vertices[np.asarray(mesh.faces)[visible_faces].ravel()] = True
    pixels_float = project_pixels(mesh, direction, size)
    # Shape generation need not reproduce the source proportions exactly.
    # Register the projected visible bounding box to the source foreground
    # before sampling; this keeps material regions aligned without an
    # object-specific landmark or hand-authored coordinates.
    source_mask = source_rgba[:, :, 3] >= 96
    source_rows, source_cols = np.nonzero(source_mask)
    projected = pixels_float[visible_vertices]
    pmin, pmax = projected.min(axis=0), projected.max(axis=0)
    sx0, sx1 = source_cols.min(), source_cols.max()
    sy0, sy1 = source_rows.min(), source_rows.max()
    pixels_float[:, 0] = sx0 + (pixels_float[:, 0] - pmin[0]) * (sx1 - sx0) / max(pmax[0] - pmin[0], 1e-9)
    pixels_float[:, 1] = sy0 + (pixels_float[:, 1] - pmin[1]) * (sy1 - sy0) / max(pmax[1] - pmin[1], 1e-9)
    pixels = np.rint(pixels_float).astype(np.int32)
    pixels[:, 0] = np.clip(pixels[:, 0], 0, size - 1)
    pixels[:, 1] = np.clip(pixels[:, 1], 0, size - 1)
    sampled = source_rgba[pixels[:, 1], pixels[:, 0]]

    protected_dark, protected_components = enclosed_dark_markings(source_rgba)
    protected_samples = protected_dark[pixels[:, 1], pixels[:, 0]] & visible_vertices
    sampled[protected_samples, :3] = 0
    sampled[protected_samples, 3] = 255

    seeds = visible_vertices & (sampled[:, 3] >= 96)
    if not seeds.any():
        raise ValueError("no visible mesh vertices overlap the source foreground")

    colours = np.zeros((len(mesh.vertices), 4), dtype=np.uint8)
    colours[seeds, :3] = sampled[seeds, :3]
    colours[seeds, 3] = 255
    hidden = ~seeds
    tree = cKDTree(np.asarray(mesh.vertices)[seeds])
    seed_colours = colours[seeds]
    hidden_ids = np.flatnonzero(hidden)
    for start in range(0, len(hidden_ids), 100_000):
        ids = hidden_ids[start:start + 100_000]
        _distance, nearest = tree.query(np.asarray(mesh.vertices)[ids], k=1)
        colours[ids] = seed_colours[nearest]
    return colours, {
        "source_sampled_vertices": int(seeds.sum()),
        "propagated_vertices": int(hidden.sum()),
        "protected_dark_components": protected_components,
        "protected_dark_vertices": int(np.count_nonzero(protected_samples)),
    }


def restore_material_chroma(mesh: trimesh.Trimesh) -> dict[str, int]:
    """Lift chromatic albedo after shadow removal, preserving neutrals/black."""
    colours = np.asarray(mesh.visual.vertex_colors).copy()
    unique, inverse = np.unique(colours[:, :3], axis=0, return_inverse=True)
    restored = unique.copy()
    changed = 0
    for index, rgb in enumerate(unique):
        h, s, v = colorsys.rgb_to_hsv(*(rgb.astype(np.float64) / 255.0))
        if v < 0.18 or s < 0.25:
            continue
        # Material colour should not inherit the photograph's shaded value.
        # Moderate saturation keeps pale red on Coral rather than skin/brown,
        # while still allowing muted LEGO colours after palette quantization.
        candidate = np.asarray(colorsys.hsv_to_rgb(h, max(s, 0.55), max(v, 0.84))) * 255.0
        restored[index] = np.clip(np.rint(candidate), 0, 255).astype(np.uint8)
        changed += int(np.count_nonzero(inverse == index))
    colours[:, :3] = restored[inverse]
    mesh.visual = ColorVisuals(vertex_colors=colours)
    return {"chroma_restored_vertices": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("qa_report", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--lego-palette", choices=("common", "full", "off"), default="common")
    args = parser.parse_args()
    mesh = trimesh.load(args.mesh, force="mesh")
    qa = json.loads(args.qa_report.read_text(encoding="utf-8"))
    with Image.open(args.source) as image:
        rgba = normalized_rgba(image)
    colours, report = projected_vertex_colours(
        mesh, rgba, qa["yaw_degrees"], qa["elevation_degrees"]
    )
    mesh.visual = ColorVisuals(vertex_colors=colours)
    mesh = flatten_illumination(mesh)
    report.update(restore_material_chroma(mesh))
    palette_report = {}
    if args.lego_palette == "common":
        palette_report = map_common_lego_colours(
            mesh, Path(__file__).resolve().parent / "LDrawLibrary" / "ldraw" / "LDConfig.ldr"
        )
    elif args.lego_palette != "off":
        raise ValueError("full palette requires the Legolization runtime; use common or off")
    # Hunyuan's shape is already Y-up. Seat a working copy on the build plane.
    mesh.apply_translation([0.0, -float(mesh.bounds[0, 1]), 0.0])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ply_path = args.output_dir / "lego_input.ply"
    glb_path = args.output_dir / "mesh_coloured.glb"
    mesh.export(ply_path)
    mesh.export(glb_path)
    direction = camera_direction(qa["yaw_degrees"], qa["elevation_degrees"])
    camera = np.asarray(mesh.bounds).mean(axis=0) + 2.5 * direction
    preview = render(mesh, size=(1000, 1000), camera=camera, light_direction=direction + [0.3, 0.7, 0.0])
    preview.save(args.output_dir / "colour_preview.png")
    final_colours = np.asarray(mesh.visual.vertex_colors)[:, :3]
    unique, counts = np.unique(final_colours, axis=0, return_counts=True)
    report.update({
        "mesh": str(args.mesh.resolve()),
        "source": str(args.source.resolve()),
        "qa_report": str(args.qa_report.resolve()),
        "yaw_degrees": qa["yaw_degrees"],
        "elevation_degrees": qa["elevation_degrees"],
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "watertight": bool(mesh.is_watertight),
        **palette_report,
        "palette_rgb_counts": [
            {"rgb": rgb.tolist(), "vertices": int(count)}
            for rgb, count in sorted(zip(unique, counts), key=lambda item: -item[1])
        ],
    })
    (args.output_dir / "colour_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
