"""Generate one isolated Paint3D depth view to verify IP-Adapter influence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from omegaconf import OmegaConf
from PIL import Image
import torch
from diffusers.models.attention_processor import (
    IPAdapterAttnProcessor,
    IPAdapterAttnProcessor2_0,
)

from paint3d_stage1_external import LocalTxt2Img


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--depth", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--ip-scale", type=float, default=1.0)
    parser.add_argument("--steps", type=int)
    args = parser.parse_args()

    cfg = OmegaConf.load(args.config).txt2img
    cfg.controlnet_units[0].condition_image_path = str(args.depth)
    cfg.ip_adapter_image_path = str(args.reference) if args.reference else None
    if args.steps is not None:
        cfg.num_inference_steps = args.steps
    os.environ["PAINT3D_IP_SCALE"] = str(args.ip_scale)

    print(
        f"reference={cfg.ip_adapter_image_path!r}, ip_scale={args.ip_scale}",
        flush=True,
    )
    controller = LocalTxt2Img(cfg)
    ip_processors = [
        processor
        for processor in controller.pipe.unet.attn_processors.values()
        if isinstance(processor, (IPAdapterAttnProcessor, IPAdapterAttnProcessor2_0))
    ]
    print(
        f"ip_processors={len(ip_processors)}, "
        f"scales={sorted({float(processor.scale) for processor in ip_processors})}",
        flush=True,
    )
    if args.reference:
        cond, uncond = controller.pipe.encode_image(
            Image.open(args.reference).convert("RGB"),
            controller.pipe._execution_device,
            1,
            False,
        )
        print(
            f"image_embed_norm={torch.linalg.vector_norm(cond.float()).item():.6f}, "
            f"uncond_embed_norm={torch.linalg.vector_norm(uncond.float()).item():.6f}",
            flush=True,
        )
    image = controller.infernece(cfg)[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print(f"saved {args.output}", flush=True)


if __name__ == "__main__":
    main()
