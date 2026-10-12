"""Make neutral, geometry-only previews of a 3D mesh without LEGO output."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pymeshlab
import trimesh
from trimesh.visual.color import ColorVisuals

from render_refinement_comparison import render


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-faces", type=int, default=50000)
    args = parser.parse_args()
    mesh = trimesh.load(args.mesh, force="mesh")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if len(mesh.faces) > args.max_faces:
        scene = pymeshlab.MeshSet()
        scene.add_mesh(pymeshlab.Mesh(
            vertex_matrix=np.asarray(mesh.vertices),
            face_matrix=np.asarray(mesh.faces),
        ))
        scene.meshing_decimation_quadric_edge_collapse(
            targetfacenum=args.max_faces,
            preservetopology=True,
        )
        reduced = scene.current_mesh()
        mesh = trimesh.Trimesh(
            vertices=reduced.vertex_matrix(),
            faces=reduced.face_matrix(),
            process=False,
        )
        mesh.export(args.output_dir / "preview_decimated.ply")
    # Shape-only GLBs often carry a dark placeholder material. Use the same
    # neutral surface colour for all engines so lighting reveals geometry.
    mesh.visual = ColorVisuals(
        vertex_colors=np.tile([220, 220, 220, 255], (len(mesh.vertices), 1))
    )
    for name, camera in {
        "iso": (1.6, 1.1, -2.2),
        "front": (0.0, 1.0, -3.0),
        "side": (3.0, 1.0, 0.0),
        "back": (0.0, 1.0, 3.0),
    }.items():
        render(mesh, size=(900, 900), camera=np.asarray(camera, dtype=float)).save(
            args.output_dir / f"preview_{name}.png"
        )
    print(f"previewed {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")


if __name__ == "__main__":
    main()
