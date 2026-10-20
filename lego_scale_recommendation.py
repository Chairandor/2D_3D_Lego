"""Print Legolization's recommended target width for a prepared mesh as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import trimesh

from legolization.mesh import MeshOptions, recommend_voxelization


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("--minimum", type=int, default=8)
    parser.add_argument("--maximum", type=int, default=128)
    args = parser.parse_args()
    if args.minimum <= 0 or args.maximum < args.minimum:
        parser.error("invalid recommendation range")

    mesh = trimesh.load_mesh(args.mesh, process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        mesh = trimesh.util.concatenate(tuple(mesh.geometry.values()))
    recommendation = recommend_voxelization(
        mesh,
        MeshOptions(
            up="y",
            auto_scale=(args.minimum, args.maximum),
            grid_phases=1,
            fill=False,
            colour_mode="sampled",
        ),
    )
    payload = recommendation.to_dict()
    payload.update(
        {
            "minimum": args.minimum,
            "maximum": args.maximum,
            "source_mtime_ns": args.mesh.stat().st_mtime_ns,
        }
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
