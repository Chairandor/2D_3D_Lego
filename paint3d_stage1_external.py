"""Run Paint3D stage 1 from local weights without modifying the upstream repo."""

from __future__ import annotations

import os
import sys
import gc
from pathlib import Path


ROOT = Path(r"E:\3D_TEST")
REPO = ROOT / "Paint3D"
WEIGHTS = ROOT / "models" / "weights" / "Paint3D"

os.environ.setdefault("HF_HOME", str(ROOT / ".cache-paint3d" / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(ROOT / ".cache-paint3d" / "torch"))
os.environ.setdefault("WARP_CACHE_PATH", str(ROOT / ".cache-paint3d" / "warp"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, str(REPO))
os.chdir(REPO)

import torch
from diffusers import (
    ControlNetModel,
    StableDiffusionControlNetInpaintPipeline,
    StableDiffusionControlNetPipeline,
)
from diffusers.schedulers import EulerAncestralDiscreteScheduler

from controlnet.diffusers_cnet_inpaint import inpaintControlNet as OfficialInpaint
from controlnet.diffusers_cnet_txt2img import txt2imgControlNet as OfficialTxt2Img
import pipeline_paint3d_stage1 as stage1


SD15 = WEIGHTS / "sd15"
IP_ADAPTER = WEIGHTS / "ip-adapter"
CONTROLNETS = {
    "depth": WEIGHTS / "controlnet-depth",
    "inpaint": WEIGHTS / "controlnet-inpaint",
}


def _require_weights() -> None:
    expected = [
        SD15 / "model_index.json",
        SD15 / "unet" / "diffusion_pytorch_model.fp16.safetensors",
        SD15 / "vae" / "diffusion_pytorch_model.fp16.safetensors",
        SD15 / "text_encoder" / "model.fp16.safetensors",
        CONTROLNETS["depth"] / "diffusion_pytorch_model.fp16.safetensors",
        CONTROLNETS["inpaint"] / "diffusion_pytorch_model.fp16.safetensors",
        IP_ADAPTER / "models" / "ip-adapter_sd15.safetensors",
        IP_ADAPTER / "models" / "image_encoder" / "model.safetensors",
    ]
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Paint3D weights:\n" + "\n".join(missing))


def _load_controlnets(config, dtype: torch.dtype):
    models = []
    for unit in config.controlnet_units:
        key = "inpaint" if "inpaint" in unit.controlnet_key.lower() else "depth"
        models.append(
            ControlNetModel.from_pretrained(
                CONTROLNETS[key],
                torch_dtype=dtype,
                variant="fp16",
                use_safetensors=True,
                local_files_only=True,
            )
        )
    return models


def _finish_pipe(pipe, config):
    if config.ip_adapter_image_path:
        pipe.load_ip_adapter(
            IP_ADAPTER,
            subfolder="models",
            weight_name="ip-adapter_sd15.safetensors",
            local_files_only=True,
        )
        pipe.set_ip_adapter_scale(float(os.environ.get("PAINT3D_IP_SCALE", "1.0")))
    pipe.scheduler = EulerAncestralDiscreteScheduler.from_pretrained(
        SD15 / "scheduler", local_files_only=True
    )
    # diffusers 0.25 replaces IP-Adapter attention processors when attention
    # slicing is enabled after load_ip_adapter(), silently disabling image
    # conditioning. CPU offload is sufficient for the 8 GB GPU.
    pipe.enable_vae_slicing()
    pipe.enable_model_cpu_offload()
    return pipe


class LocalTxt2Img(OfficialTxt2Img):
    def __init__(self, config, torch_dtype=torch.float16):
        controls = _load_controlnets(config, torch_dtype)
        pipe = StableDiffusionControlNetPipeline.from_pretrained(
            SD15,
            controlnet=controls,
            safety_checker=None,
            feature_extractor=None,
            requires_safety_checker=False,
            torch_dtype=torch_dtype,
            variant="fp16",
            use_safetensors=True,
            local_files_only=True,
        )
        self.pipe = _finish_pipe(pipe, config)

    def infernece(self, config):
        images = super().infernece(config)
        # Stage 1 uses the depth txt2img pipeline only once. Release it before
        # the inpaint pipeline is materialized to keep host/GPU memory bounded.
        self.pipe = None
        gc.collect()
        torch.cuda.empty_cache()
        return images


class LocalInpaint(OfficialInpaint):
    def __init__(self, config, torch_dtype=torch.float16):
        self._config = config
        self._torch_dtype = torch_dtype
        self.pipe = None

    def _ensure_pipe(self):
        if self.pipe is not None:
            return
        config = self._config
        torch_dtype = self._torch_dtype
        controls = _load_controlnets(config, torch_dtype)
        pipe = StableDiffusionControlNetInpaintPipeline.from_pretrained(
            SD15,
            controlnet=controls,
            safety_checker=None,
            feature_extractor=None,
            requires_safety_checker=False,
            torch_dtype=torch_dtype,
            variant="fp16",
            use_safetensors=True,
            local_files_only=True,
        )
        self.pipe = _finish_pipe(pipe, config)

    def infernece(self, config):
        self._ensure_pipe()
        return super().infernece(config)


if __name__ == "__main__":
    _require_weights()
    stage1.txt2imgControlNet = LocalTxt2Img
    stage1.inpaintControlNet = LocalInpaint
    stage1.main()
