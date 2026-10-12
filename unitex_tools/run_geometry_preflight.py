"""Render UniTEX geometry controls without loading FLUX or altering upstream."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    mesh = args.mesh.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(repo))
    os.chdir(repo)

    from TextureTools.texturetools.geometry.uv.uv_atlas import preprocess_blank_mesh
    from TextureTools.texturetools.video.export_nvdiffrast_video import VideoExporter

    processed_mesh = output / "processed_mesh.obj"
    preprocess_blank_mesh(
        str(mesh),
        str(processed_mesh),
        min_faces=20_000,
        max_faces=200_000,
        scale=0.95,
    )

    rendered = VideoExporter().export_condition(
        str(mesh),
        geometry_scale=0.95,
        n_views=6,
        n_rows=2,
        n_cols=3,
        H=512,
        W=512,
        fov_deg=49.1,
        scale=1.0,
        perspective=False,
        orbit=False,
        background="grey",
        return_info=False,
        return_image=True,
        return_mesh=False,
        return_camera=True,
    )
    rendered["alpha"].save(output / "mv_alpha.png")
    rendered["ccm"].save(output / "mv_ccm.png")
    rendered["normal"].save(output / "mv_normal.png")
    torch.save(
        {
            "c2ws": rendered["c2ws"],
            "intrinsics": rendered["intrinsics"],
            "perspective": rendered["perspective"],
        },
        output / "camera_info.pth",
    )
    print(f"UniTEX geometry preflight output: {output}", flush=True)


if __name__ == "__main__":
    main()
