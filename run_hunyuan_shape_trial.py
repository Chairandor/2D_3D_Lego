"""Isolated, shape-only Hunyuan3D 2 mini trial; never builds LEGO.

The downloaded upstream repository is imported read-only. Model weights and
outputs stay beneath E:\\3D_TEST; neither SF3D nor legolization is modified.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parent
HY3D_ROOT = ROOT / "Hunyuan3D-2"
os.environ["HF_HOME"] = str(ROOT / "weights" / "hf_hunyuan")
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "30"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "30"
sys.path.insert(0, str(HY3D_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a shape-only 2D-to-3D model")
    parser.add_argument("image", type=Path, help="RGBA image with a pre-reviewed foreground mask")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--octree-resolution", type=int, default=384)
    parser.add_argument("--cpu-offload", action="store_true")
    args = parser.parse_args()
    if not args.image.is_file():
        parser.error(f"image not found: {args.image}")
    if not HY3D_ROOT.is_dir():
        parser.error(f"upstream Hunyuan3D repository not found: {HY3D_ROOT}")

    import torch
    from huggingface_hub import snapshot_download
    from PIL import Image
    from requests.exceptions import ChunkedEncodingError, ConnectionError, ReadTimeout
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline

    if not torch.cuda.is_available():
        parser.error("CUDA is required for this local shape trial")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(args.image).convert("RGBA")
    print(f"input={args.image} size={image.size} CUDA={torch.version.cuda}", flush=True)
    # Upstream's default fetch downloads both .ckpt and .safetensors. Fetch
    # only the safe weights actually used by this trial, then pass the local
    # snapshot to its unmodified loader.
    subfolder = "hunyuan3d-dit-v2-mini"
    for attempt in range(1, 9):
        try:
            model_snapshot = snapshot_download(
                repo_id="tencent/Hunyuan3D-2mini",
                allow_patterns=[
                    f"{subfolder}/config.yaml",
                    f"{subfolder}/model.fp16.safetensors",
                ],
                max_workers=1,
            )
            break
        except (ChunkedEncodingError, ConnectionError, ReadTimeout) as exc:
            if attempt == 8:
                raise
            print(f"weight download interrupted ({attempt}/8): {exc}; resuming", flush=True)
            time.sleep(3)
    pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        model_snapshot,
        subfolder=subfolder,
    )
    if args.cpu_offload:
        pipeline.enable_model_cpu_offload()
    generator = torch.Generator(device="cuda").manual_seed(args.seed)
    mesh = pipeline(
        image=image,
        num_inference_steps=args.steps,
        generator=generator,
        octree_resolution=args.octree_resolution,
        enable_pbar=False,
    )[0]
    output_mesh = args.output_dir / "mesh_shape_only.glb"
    mesh.export(output_mesh)
    report = {
        "input": str(args.image.resolve()),
        "output": str(output_mesh.resolve()),
        "model": "tencent/Hunyuan3D-2mini/hunyuan3d-dit-v2-mini",
        "steps": args.steps,
        "seed": args.seed,
        "octree_resolution": args.octree_resolution,
        "cpu_offload": args.cpu_offload,
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
    }
    (args.output_dir / "run.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
