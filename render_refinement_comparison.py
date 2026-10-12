"""Quick, dependency-light mesh comparison for review (not a photoreal renderer)."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import trimesh


def render(
    mesh: trimesh.Trimesh,
    *,
    size: tuple[int, int],
    camera: np.ndarray,
    light_direction: np.ndarray | None = None,
) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, (234, 237, 240))
    draw = ImageDraw.Draw(image)
    vertices = np.asarray(mesh.vertices)
    centre = np.asarray(mesh.bounds).mean(axis=0)
    forward = centre - camera
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    points = vertices - centre
    xy = np.column_stack((points @ right, points @ up))
    scale = min(0.82 * width / np.ptp(xy[:, 0]), 0.82 * height / np.ptp(xy[:, 1]))
    pixels = np.column_stack((width / 2 + xy[:, 0] * scale, height / 2 - xy[:, 1] * scale))
    vertex_colours = getattr(mesh.visual, "vertex_colors", None)
    colours = (
        np.asarray(vertex_colours)[:, :3]
        if vertex_colours is not None
        else np.tile([220, 220, 220], (len(vertices), 1))
    )
    faces = np.asarray(mesh.faces)
    centres = np.asarray(mesh.triangles_center)
    visible = (np.asarray(mesh.face_normals) * (camera - centres)).sum(axis=1) > 0
    face_ids = np.flatnonzero(visible)
    depths = (centres[face_ids] - camera) @ forward
    light = np.asarray(
        [0.25, 0.85, -0.46] if light_direction is None else light_direction,
        dtype=float,
    )
    light /= np.linalg.norm(light)
    for face_id in face_ids[np.argsort(depths)[::-1]]:
        face = faces[face_id]
        base = colours[face].astype(float).mean(axis=0)
        diffuse = max(0.0, float(mesh.face_normals[face_id] @ light))
        brightness = (
            0.78 + 0.22 * diffuse
            if light_direction is None
            else 0.47 + 0.53 * diffuse
        )
        colour = tuple(np.clip(np.rint(base * brightness), 0, 255).astype(np.uint8))
        draw.polygon([tuple(pixels[index]) for index in face], fill=colour)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("refined", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--camera", nargs=3, type=float, default=[1.6, 1.1, -2.2])
    args = parser.parse_args()
    baseline = trimesh.load(args.baseline, force="mesh")
    refined = trimesh.load(args.refined, force="mesh")
    panel = (650, 650)
    camera = np.asarray(args.camera, dtype=float)
    combined = Image.new("RGB", (panel[0] * 2, panel[1]), (234, 237, 240))
    combined.paste(render(baseline, size=panel, camera=camera), (0, 0))
    combined.paste(render(refined, size=panel, camera=camera), (panel[0], 0))
    draw = ImageDraw.Draw(combined)
    draw.text((20, 18), "BEFORE", fill=(20, 20, 20))
    draw.text((panel[0] + 20, 18), "REFINED", fill=(20, 20, 20))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    combined.save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
