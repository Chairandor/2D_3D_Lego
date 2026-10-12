"""Create a topology-preserving working copy of a dense shape mesh for LEGO."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pymeshlab
import trimesh


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-faces", type=int, default=150_000)
    args = parser.parse_args()
    mesh = trimesh.load(args.mesh, force="mesh")
    before = {"vertices": len(mesh.vertices), "faces": len(mesh.faces), "watertight": bool(mesh.is_watertight)}
    if len(mesh.faces) > args.max_faces:
        scene = pymeshlab.MeshSet()
        scene.add_mesh(pymeshlab.Mesh(
            vertex_matrix=np.asarray(mesh.vertices),
            face_matrix=np.asarray(mesh.faces),
        ))
        scene.meshing_decimation_quadric_edge_collapse(
            targetfacenum=args.max_faces,
            preservetopology=True,
            preservenormal=True,
        )
        reduced = scene.current_mesh()
        mesh = trimesh.Trimesh(
            vertices=reduced.vertex_matrix(),
            faces=reduced.face_matrix(),
            process=True,
        )
    trimesh.repair.fix_normals(mesh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(args.output)
    report = {
        "source": str(args.mesh.resolve()),
        "output": str(args.output.resolve()),
        "before": before,
        "after": {
            "vertices": len(mesh.vertices),
            "faces": len(mesh.faces),
            "watertight": bool(mesh.is_watertight),
            "components": len(mesh.split(only_watertight=False)),
        },
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
