"""Render shadow-free transparent reference images from a coloured mesh.

This is intentionally a small external review utility.  It uses an
orthographic camera, the mesh's stored colours or diffuse texture, a
transparent background and no cast shadows, so the result is suitable as an
albedo-like image prompt.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import trimesh


VIEWS = {
    "front": (0.0, 0.35, -3.0),
    "front_left": (-2.2, 1.15, -2.2),
    "front_right": (2.2, 1.15, -2.2),
    "left": (-3.0, 0.35, 0.0),
    "right": (3.0, 0.35, 0.0),
    "back_left": (-2.2, 1.15, 2.2),
    "back_right": (2.2, 1.15, 2.2),
    "back": (0.0, 0.35, 3.0),
}


def face_colours(mesh: trimesh.Trimesh) -> np.ndarray:
    """Return one unlit RGBA colour per triangle.

    Textured OBJ files are sampled at the centre of each triangle in UV space.
    This is deliberate for the current low-poly/palette-textured assets: it
    preserves the authored material colour without adding renderer lighting.
    """
    faces = np.asarray(mesh.faces)
    visual = mesh.visual
    if visual.kind == "texture":
        texture = getattr(visual.material, "image", None)
        uv = getattr(visual, "uv", None)
        if texture is None or uv is None:
            raise ValueError("Textured mesh is missing its texture image or UVs")
        rgba = np.asarray(texture.convert("RGBA"))
        uv_centres = np.asarray(uv, dtype=float)[faces].mean(axis=1)
        height, width = rgba.shape[:2]
        x = np.clip(np.rint(uv_centres[:, 0] * (width - 1)), 0, width - 1).astype(int)
        y = np.clip(
            np.rint((1.0 - uv_centres[:, 1]) * (height - 1)), 0, height - 1
        ).astype(int)
        return rgba[y, x]

    vertex_colours = np.asarray(visual.vertex_colors)
    if len(vertex_colours) != len(mesh.vertices):
        raise ValueError("Mesh must contain a diffuse texture or vertex colours")
    return np.clip(
        np.rint(vertex_colours[faces, :4].astype(float).mean(axis=1)), 0, 255
    ).astype(np.uint8)


def render_flat(
    mesh: trimesh.Trimesh,
    camera_direction: tuple[float, float, float],
    *,
    size: int = 1024,
    fill: float = 0.90,
    supersample: int = 3,
) -> Image.Image:
    render_size = size * supersample
    colour_buffer = np.zeros((render_size, render_size, 4), dtype=np.uint8)
    depth_buffer = np.full((render_size, render_size), np.inf, dtype=np.float32)

    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces)
    centre = np.asarray(mesh.bounds, dtype=float).mean(axis=0)
    direction = np.asarray(camera_direction, dtype=float)
    direction /= np.linalg.norm(direction)
    camera = centre + direction * 4.0
    forward = -direction

    world_up = np.asarray([0.0, 1.0, 0.0])
    right = np.cross(forward, world_up)
    if np.linalg.norm(right) < 1e-8:
        world_up = np.asarray([0.0, 0.0, 1.0])
        right = np.cross(forward, world_up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    up /= np.linalg.norm(up)

    centred = vertices - centre
    xy = np.column_stack((centred @ right, centred @ up))
    span = np.maximum(np.ptp(xy, axis=0), 1e-8)
    scale = min(fill * render_size / span[0], fill * render_size / span[1])
    pixels = np.column_stack(
        (
            render_size / 2 + xy[:, 0] * scale,
            render_size / 2 - xy[:, 1] * scale,
        )
    )

    triangle_colours = face_colours(mesh)

    face_centres = np.asarray(mesh.triangles_center)
    visible = (np.asarray(mesh.face_normals) * (camera - face_centres)).sum(axis=1) > 0
    vertex_depths = (vertices - camera) @ forward
    for face_id in np.flatnonzero(visible):
        face = faces[face_id]
        colour = tuple(triangle_colours[face_id])
        triangle = pixels[face]
        minimum = np.maximum(np.floor(triangle.min(axis=0)).astype(int), 0)
        maximum = np.minimum(
            np.ceil(triangle.max(axis=0)).astype(int), render_size - 1
        )
        if np.any(maximum < minimum):
            continue

        x0, y0 = triangle[0]
        x1, y1 = triangle[1]
        x2, y2 = triangle[2]
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1e-10:
            continue

        xs = np.arange(minimum[0], maximum[0] + 1, dtype=np.float32) + 0.5
        ys = np.arange(minimum[1], maximum[1] + 1, dtype=np.float32) + 0.5
        grid_x, grid_y = np.meshgrid(xs, ys)
        weight0 = (
            (y1 - y2) * (grid_x - x2) + (x2 - x1) * (grid_y - y2)
        ) / denominator
        weight1 = (
            (y2 - y0) * (grid_x - x2) + (x0 - x2) * (grid_y - y2)
        ) / denominator
        weight2 = 1.0 - weight0 - weight1
        inside = (weight0 >= -1e-6) & (weight1 >= -1e-6) & (weight2 >= -1e-6)
        if not inside.any():
            continue

        depths = (
            weight0 * vertex_depths[face[0]]
            + weight1 * vertex_depths[face[1]]
            + weight2 * vertex_depths[face[2]]
        )
        y_slice = slice(minimum[1], maximum[1] + 1)
        x_slice = slice(minimum[0], maximum[0] + 1)
        current = depth_buffer[y_slice, x_slice]
        nearer = inside & (depths < current)
        current[nearer] = depths[nearer]
        colour_buffer[y_slice, x_slice][nearer] = colour

    canvas = Image.fromarray(colour_buffer, mode="RGBA")
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def make_contact_sheet(images: dict[str, Image.Image], output: Path) -> None:
    tile = 360
    sheet = Image.new("RGBA", (tile * 4, tile * 2), (235, 237, 240, 255))
    draw = ImageDraw.Draw(sheet)
    for index, (name, image) in enumerate(images.items()):
        panel = image.resize((tile, tile), Image.Resampling.LANCZOS)
        x = (index % 4) * tile
        y = (index // 4) * tile
        sheet.alpha_composite(panel, (x, y))
        draw.text((x + 12, y + 12), name, fill=(20, 20, 20, 255))
    sheet.convert("RGB").save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--view", choices=(*VIEWS, "all"), default="all")
    args = parser.parse_args()

    mesh = trimesh.load(args.mesh, force="mesh", process=False)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = VIEWS if args.view == "all" else {args.view: VIEWS[args.view]}
    rendered: dict[str, Image.Image] = {}
    for name, camera in selected.items():
        image = render_flat(mesh, camera)
        image.save(args.output_dir / f"reference_{name}.png")
        rendered[name] = image
    if args.view == "all":
        make_contact_sheet(rendered, args.output_dir / "contact_sheet.png")

    print(f"mesh={args.mesh}")
    print(f"vertices={len(mesh.vertices)} faces={len(mesh.faces)} bounds={mesh.bounds.tolist()}")
    print(f"outputs={args.output_dir}")


if __name__ == "__main__":
    main()
