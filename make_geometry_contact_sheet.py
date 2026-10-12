"""Build a neutral source/SF3D/alternative 3D review sheet; no LEGO output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
import trimesh
from trimesh.visual.color import ColorVisuals

from render_refinement_comparison import render
from shape_qa import preview_mesh


def source_panel(path: Path, size: int) -> Image.Image:
    rgba = Image.open(path).convert("RGBA")
    alpha = np.asarray(rgba)[:, :, 3]
    rows, cols = np.nonzero(alpha >= 128)
    rgba = rgba.crop((cols.min(), rows.min(), cols.max() + 1, rows.max() + 1))
    fitted = ImageOps.contain(rgba, (int(size * 0.86), int(size * 0.86)))
    panel = Image.new("RGB", (size, size), (234, 237, 240))
    x = (size - fitted.width) // 2
    y = (size - fitted.height) // 2
    panel.paste(fitted, (x, y), fitted)
    return panel


def mesh_panel(mesh_path: Path, report_path: Path, size: int) -> Image.Image:
    mesh = preview_mesh(trimesh.load(mesh_path, force="mesh"), max_faces=30000)
    mesh.visual = ColorVisuals(
        vertex_colors=np.tile([220, 220, 220, 255], (len(mesh.vertices), 1))
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    yaw = np.deg2rad(report["yaw_degrees"])
    elevation = np.deg2rad(report["elevation_degrees"])
    direction = np.array([
        np.sin(yaw) * np.cos(elevation),
        np.sin(elevation),
        np.cos(yaw) * np.cos(elevation),
    ])
    camera = np.asarray(mesh.bounds).mean(axis=0) + 2.5 * direction
    right = np.cross(-direction, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    light = direction + 0.7 * right + np.array([0.0, 0.8, 0.0])
    return render(mesh, size=(size, size), camera=camera, light_direction=light)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("foreground", type=Path)
    parser.add_argument("sf3d_mesh", type=Path)
    parser.add_argument("sf3d_qa", type=Path)
    parser.add_argument("alternate_mesh", type=Path)
    parser.add_argument("alternate_qa", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    size = 620
    sheet = Image.new("RGB", (size * 3, size), (234, 237, 240))
    panels = (
        source_panel(args.foreground, size),
        mesh_panel(args.sf3d_mesh, args.sf3d_qa, size),
        mesh_panel(args.alternate_mesh, args.alternate_qa, size),
    )
    for index, (panel, label) in enumerate(zip(panels, ("SOURCE", "SF3D", "HUNYUAN MINI"))):
        sheet.paste(panel, (index * size, 0))
        ImageDraw.Draw(sheet).text((index * size + 18, 18), label, fill=(25, 25, 25))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output)
    print(args.output)


if __name__ == "__main__":
    main()
