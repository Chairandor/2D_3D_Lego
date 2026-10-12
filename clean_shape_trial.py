"""Save a review-only cleaned copy of a generated shape; preserve its raw GLB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import trimesh

from shape_component_cleanup import remove_negligible_components


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    mesh = trimesh.load(args.source, force="mesh")
    cleaned, report = remove_negligible_components(mesh)
    target = args.source.with_name("mesh_shape_review_clean.glb")
    cleaned.export(target)
    args.source.with_name("cleanup.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
