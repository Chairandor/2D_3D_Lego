"""Run UniTEX stage-1 and texture projection without editing upstream files.

The two phases are intentionally separable.  ``stage1`` creates the six-view
albedo sheet for inspection; ``project`` turns an accepted sheet into a
textured GLB.  Reusing the same output directory resumes from the cached
stage-1 files.
"""

from __future__ import annotations

import argparse
import gc
import os
import shutil
import sys
from pathlib import Path

import torch
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("stage1", "project", "all"), default="all")
    parser.add_argument("--projection", choices=("ltm", "direct"), default="ltm")
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--seed", type=int, default=63)
    parser.add_argument(
        "--reference-mode",
        choices=("official", "rgba"),
        default="rgba",
        help="rgba preserves an already approved alpha mask",
    )
    return parser.parse_args()


class PassthroughRMBG:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __call__(self, image: Image.Image) -> Image.Image:
        return image


def preprocess_rgba(self, save_dir: str, input_image_path: str, scale=0.95, color="grey"):
    """Fit an accepted transparent cutout without segmenting it again."""
    del self
    image = Image.open(input_image_path).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError(f"Reference image has an empty alpha mask: {input_image_path}")
    subject = image.crop(bbox)
    maximum = max(1, int(round(1024 * float(scale))))
    subject.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    transparent = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    transparent.alpha_composite(
        subject,
        ((1024 - subject.width) // 2, (1024 - subject.height) // 2),
    )
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


def install_open3d_ray_backend() -> None:
    """Use Open3D ray casting when Slang binaries exceed the host GLIBC.

    UniTEX only needs closest triangle ids (plus conventional hit metadata)
    during inverse rendering. Open3D's BVH provides the same operation and is
    already a UniTEX dependency, so this keeps the upstream checkout untouched.
    """
    import numpy as np
    import open3d as o3d
    import TextureTools.texturetools.mesh.structure_v2 as mesh_structure

    class Open3DRayTracing:
        def __init__(self, vertices: torch.Tensor, faces: torch.Tensor, **_kwargs) -> None:
            self.update_raw(vertices, faces)

        def update_raw(self, vertices: torch.Tensor, faces: torch.Tensor) -> None:
            vertex_array = vertices.detach().to(dtype=torch.float32, device="cpu").contiguous().numpy()
            face_array = faces.detach().to(dtype=torch.int64, device="cpu").contiguous().numpy()
            triangle_mesh = o3d.t.geometry.TriangleMesh(
                o3d.core.Tensor(vertex_array, dtype=o3d.core.Dtype.Float32),
                o3d.core.Tensor(face_array, dtype=o3d.core.Dtype.Int64),
            )
            self.scene = o3d.t.geometry.RaycastingScene()
            self.scene.add_triangles(triangle_mesh)

        def intersects_closest(
            self,
            rays_o: torch.Tensor,
            rays_d: torch.Tensor,
            stream_compaction: bool = False,
        ):
            del stream_compaction
            rays_o, rays_d = torch.broadcast_tensors(rays_o, rays_d)
            batch_shape = tuple(rays_o.shape[:-1])
            flat_o = rays_o.detach().to(dtype=torch.float32, device="cpu").reshape(-1, 3)
            flat_d = rays_d.detach().to(dtype=torch.float32, device="cpu").reshape(-1, 3)
            hits, fronts, triangle_ids, locations, triangle_uvs = [], [], [], [], []
            chunk_size = 500_000
            for start in range(0, flat_o.shape[0], chunk_size):
                origins = flat_o[start : start + chunk_size].numpy()
                directions = flat_d[start : start + chunk_size].numpy()
                rays = np.concatenate((origins, directions), axis=-1)
                result = self.scene.cast_rays(
                    o3d.core.Tensor(rays, dtype=o3d.core.Dtype.Float32)
                )
                t_hit = result["t_hit"].numpy().astype(np.float32, copy=False)
                hit = np.isfinite(t_hit)
                triangle_id = result["primitive_ids"].numpy().astype(np.int64, copy=True)
                triangle_id[~hit] = -1
                normals = result["primitive_normals"].numpy().astype(np.float32, copy=False)
                uv = result["primitive_uvs"].numpy().astype(np.float32, copy=False)
                location = origins + directions * t_hit[:, None]
                front = np.sum(directions * normals, axis=-1) < 0.0
                hits.append(torch.from_numpy(hit.copy()))
                fronts.append(torch.from_numpy(front.copy()))
                triangle_ids.append(torch.from_numpy(triangle_id))
                locations.append(torch.from_numpy(location.copy()))
                triangle_uvs.append(torch.from_numpy(uv.copy()))

            target = rays_o.device
            hit = torch.cat(hits).reshape(batch_shape).to(target)
            front = torch.cat(fronts).reshape(batch_shape).to(target)
            triangle_id = torch.cat(triangle_ids).reshape(batch_shape).to(target)
            location = torch.cat(locations).reshape(batch_shape + (3,)).to(target)
            uv = torch.cat(triangle_uvs).reshape(batch_shape + (2,)).to(target)
            return hit, front, triangle_id, location, uv

    mesh_structure.RayTracing = Open3DRayTracing
    print("UniTEX ray tracing backend: Open3D BVH", flush=True)


def make_flux_builder(args: argparse.Namespace):
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
        from diffusers import BitsAndBytesConfig, FluxTransformer2DModel
        from flux_piplines.texturing.pipeline import PBRFluxPipeline
        from huggingface_hub import hf_hub_download

        quantization = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model_id = os.environ.get("UNITEX_FLUX_MODEL", "black-forest-labs/FLUX.1-dev")
        local_only = Path(model_id).exists()
        transformer = FluxTransformer2DModel.from_pretrained(
            model_id,
            subfolder="transformer",
            quantization_config=quantization,
            torch_dtype=torch.bfloat16,
            local_files_only=local_only,
        )
        flux = PBRFluxPipeline.from_pretrained(
            model_id,
            transformer=transformer,
            text_encoder=None,
            text_encoder_2=None,
            torch_dtype=torch.bfloat16,
            local_files_only=local_only,
        )
        adapters = []
        texture_weights = []
        delight_weights = []
        hub_offline = os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        for filename, name, texture_weight, delight_weight in (
            ("mv_lora_weights.safetensors", "texture", 1.0, 0.0),
            ("delight_lora_weights.safetensors", "delight", 0.0, 1.0),
        ):
            weight = Path(
                hf_hub_download(
                    repo_id="lyxun/UniTEX",
                    filename=filename,
                    repo_type="model",
                    local_files_only=hub_offline,
                )
            )
            flux.load_lora_weights(str(weight.parent), weight_name=weight.name, adapter_name=name)
            adapters.append(name)
            texture_weights.append(texture_weight)
            delight_weights.append(delight_weight)
        if add_lora_path:
            for index, path in enumerate(add_lora_path):
                name = f"add_lora_{index}"
                flux.load_lora_weights(path, adapter_name=name)
                adapters.append(name)
                weight = add_lora_weights[index]
                texture_weights.append(weight)
                delight_weights.append(weight)
        flux.transformer.__class__.__name__ = "FluxTransformer2DModel"
        flux._num_inference_steps = args.steps
        flux._exclude_from_cpu_offload = ["transformer"]
        flux.enable_model_cpu_offload(device="cuda")
        return flux, texture_weights, delight_weights, adapters

    return build_quantized_pipeline


def copy_outputs(cache: Path, output: Path) -> None:
    for filename in (
        "rembg_image.png",
        "processed_image.png",
        "mv_alpha.png",
        "mv_normal.png",
        "mv_ccm.png",
        "mv_rgb_w_light.png",
        "mv_rgb.png",
        "completed_uv.png",
        "valid_uv_mask.png",
        "visable_uv_mask.png",
        "textured_mesh.glb",
    ):
        source = cache / filename
        if source.exists():
            shutil.copy2(source, output / filename)


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    image = args.image.resolve()
    mesh = args.mesh.resolve()
    output = args.output.resolve()
    cache = output / "cache"
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo))
    os.chdir(repo)

    import pipeline as upstream

    original_build_ltm = upstream.build_ltm
    if args.reference_mode == "rgba":
        upstream.RMBG2 = PassthroughRMBG
        upstream.RGBTextureFullPipelineBase.preprocess_reference_image = preprocess_rgba

    if args.phase in ("stage1", "all"):
        upstream.build_pipeline = make_flux_builder(args)
        upstream.build_ltm = lambda *_args, **_kwargs: (None, True, False)
        runner = upstream.RGBTextureFullPipeline(
            super_resolutions=False,
            filt_gradient_points=False,
            filt_large_angle_points=True,
            seed=args.seed,
        )
        runner.step_1_1(str(cache), str(image), str(mesh), clear_cache=False)
        copy_outputs(cache, output)
        del runner
        gc.collect()
        torch.cuda.empty_cache()

    if args.phase in ("project", "all"):
        required = ("processed_mesh.obj", "mv_rgb.png", "camera_info.pth")
        missing = [name for name in required if not (cache / name).is_file()]
        if missing:
            raise FileNotFoundError(f"UniTEX stage-1 cache is incomplete: {missing}")
        # Projection does not use FLUX.  Avoid reloading it, but construct the
        # official LTM when the selected projection mode needs unseen-surface fill.
        upstream.build_pipeline = lambda *_args, **_kwargs: (None, [], [], [])
        upstream.build_ltm = (
            original_build_ltm
            if args.projection == "ltm"
            else (lambda *_args, **_kwargs: (None, True, False))
        )
        if os.environ.get("UNITEX_RAY_BACKEND", "").lower() == "open3d":
            install_open3d_ray_backend()
        runner = upstream.RGBTextureFullPipeline(
            super_resolutions=False,
            filt_gradient_points=False,
            filt_large_angle_points=True,
            seed=args.seed,
        )
        processed_mesh = cache / "processed_mesh.obj"
        if args.projection == "ltm":
            runner.sampling_on_mesh(str(cache), str(processed_mesh))
            runner.infer_field(
                str(cache),
                str(processed_mesh),
                str(cache / "sharp_pcd_fps.ply"),
                str(cache / "coarse_pcd_fps.ply"),
                str(cache / "mv_alpha.png"),
                str(cache / "mv_ccm.png"),
                str(cache / "mv_rgb.png"),
                base_or_inpainting=False,
                test_query_field=False,
                save_point_clouds=False,
            )
        runner.reproject_and_query_field(
            str(cache),
            str(processed_mesh),
            str(cache / "mv_rgb.png"),
            str(cache / "camera_info.pth"),
            inpainting=args.projection == "ltm",
        )
        copy_outputs(cache, output)

    print(f"UniTEX {args.phase} output: {output}", flush=True)


if __name__ == "__main__":
    main()
