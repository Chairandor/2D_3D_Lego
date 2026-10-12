"""Run only UniTEX multi-view generation without modifying its repository.

The wrapper deliberately skips LTM construction and final texture projection.
It is used as an acceptance gate: inspect the six-view RGB sheet before any
later texturing stage is allowed to run.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--seed", type=int, default=63)
    parser.add_argument(
        "--reference-mode",
        choices=("official", "rgba"),
        default="official",
        help="official runs UniTEX RMBG-2.0; rgba preserves an existing alpha mask",
    )
    return parser.parse_args()


class PassthroughRMBG:
    """Placeholder used when the input already has an accepted alpha mask."""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __call__(self, image: Image.Image) -> Image.Image:
        return image


def preprocess_rgba(self, save_dir: str, input_image_path: str, scale=0.95, color="grey"):
    """Fit an existing transparent cutout without running another segmenter."""
    del self
    image = Image.open(input_image_path).convert("RGBA")
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError(f"Reference image has an empty alpha mask: {input_image_path}")
    subject = image.crop(bbox)
    maximum = max(1, int(round(1024 * float(scale))))
    subject.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    transparent = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    offset = ((1024 - subject.width) // 2, (1024 - subject.height) // 2)
    transparent.alpha_composite(subject, offset)
    transparent.save(os.path.join(save_dir, "rembg_image.png"))

    backgrounds = {
        "grey": (127, 127, 127, 255),
        "white": (255, 255, 255, 255),
        "black": (0, 0, 0, 255),
    }
    composite = Image.new("RGBA", transparent.size, backgrounds.get(color, backgrounds["grey"]))
    composite.alpha_composite(transparent)
    composite.convert("RGB").resize((512, 512), Image.Resampling.LANCZOS).save(
        os.path.join(save_dir, "processed_image.png")
    )


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    sys.path.insert(0, str(repo))
    os.chdir(repo)

    import pipeline as upstream
    from diffusers import BitsAndBytesConfig, FluxTransformer2DModel
    from flux_piplines.texturing.pipeline import PBRFluxPipeline
    from huggingface_hub import hf_hub_download

    def build_quantized_pipeline(
        pretrain_models=None,
        pipeline_name="texture_plus",
        model="rgb",
        super_resolutions=False,
        add_lora_path=None,
        add_lora_weights=None,
        speedup_mode=False,
    ):
        del pretrain_models, pipeline_name, model, super_resolutions, speedup_mode
        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model_id = os.environ.get("UNITEX_FLUX_MODEL", "black-forest-labs/FLUX.1-dev")
        local_files_only = Path(model_id).exists()
        transformer = FluxTransformer2DModel.from_pretrained(
            model_id,
            subfolder="transformer",
            quantization_config=quantization,
            torch_dtype=torch.bfloat16,
            local_files_only=local_files_only,
        )
        flux = PBRFluxPipeline.from_pretrained(
            model_id,
            transformer=transformer,
            text_encoder=None,
            text_encoder_2=None,
            torch_dtype=torch.bfloat16,
            local_files_only=local_files_only,
        )
        texture_lora = hf_hub_download(
            repo_id="lyxun/UniTEX",
            filename="mv_lora_weights.safetensors",
            repo_type="model",
        )
        delight_lora = hf_hub_download(
            repo_id="lyxun/UniTEX",
            filename="delight_lora_weights.safetensors",
            repo_type="model",
        )
        texture_lora_path = Path(texture_lora)
        delight_lora_path = Path(delight_lora)
        flux.load_lora_weights(
            str(texture_lora_path.parent),
            weight_name=texture_lora_path.name,
            adapter_name="texture",
        )
        flux.load_lora_weights(
            str(delight_lora_path.parent),
            weight_name=delight_lora_path.name,
            adapter_name="delight",
        )
        adapter_names = ["texture", "delight"]
        texture_weights = [1.0, 0.0]
        delight_weights = [0.0, 1.0]
        if add_lora_path:
            for index, path in enumerate(add_lora_path):
                name = f"add_lora_{index}"
                flux.load_lora_weights(path, adapter_name=name)
                adapter_names.append(name)
                weight = add_lora_weights[index]
                texture_weights.append(weight)
                delight_weights.append(weight)
        flux.transformer.__class__.__name__ = "FluxTransformer2DModel"
        flux._num_inference_steps = args.steps
        flux._exclude_from_cpu_offload = ["transformer"]
        flux.enable_model_cpu_offload(device="cuda")
        return flux, texture_weights, delight_weights, adapter_names

    # Keep the upstream repository untouched while replacing only construction
    # hooks for this process.
    upstream.build_pipeline = build_quantized_pipeline
    upstream.build_ltm = lambda *_args, **_kwargs: (None, True, False)
    if args.reference_mode == "rgba":
        upstream.RMBG2 = PassthroughRMBG
        upstream.RGBTextureFullPipelineBase.preprocess_reference_image = preprocess_rgba

    output = args.output.resolve()
    cache = output / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    runner = upstream.CustomRGBTextureFullPipeline(
        super_resolutions=False,
        filt_gradient_points=False,
        filt_large_angle_points=True,
        seed=args.seed,
    )
    runner.step_1_1(
        str(cache),
        str(args.image.resolve()),
        str(args.mesh.resolve()),
        clear_cache=False,
    )
    for filename in (
        "rembg_image.png",
        "processed_image.png",
        "mv_normal.png",
        "mv_ccm.png",
        "mv_rgb_w_light.png",
        "mv_rgb.png",
    ):
        source = cache / filename
        if source.exists():
            shutil.copy2(source, output / filename)
    print(f"UniTEX stage-1 review output: {output}", flush=True)


if __name__ == "__main__":
    main()
