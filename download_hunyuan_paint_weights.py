"""Download only the native Hunyuan3D-2 Paint weights used by our pipeline.

The upstream repository contains duplicate PyTorch and safetensors variants.
Keeping an explicit allow-list avoids downloading both copies to the system disk.
"""

from pathlib import Path

from huggingface_hub import snapshot_download


REPO_ID = "tencent/Hunyuan3D-2"
TARGET = Path(r"E:\3D_TEST\models\weights\Hunyuan3D-2-paint")

ALLOW_PATTERNS = [
    # Image delighting model (shadow / lighting removal).
    "hunyuan3d-delight-v2-0/feature_extractor/*",
    "hunyuan3d-delight-v2-0/model_index.json",
    "hunyuan3d-delight-v2-0/scheduler/*",
    "hunyuan3d-delight-v2-0/text_encoder/config.json",
    "hunyuan3d-delight-v2-0/text_encoder/model.safetensors",
    "hunyuan3d-delight-v2-0/tokenizer/*",
    "hunyuan3d-delight-v2-0/unet/config.json",
    "hunyuan3d-delight-v2-0/unet/diffusion_pytorch_model.safetensors",
    "hunyuan3d-delight-v2-0/vae/config.json",
    "hunyuan3d-delight-v2-0/vae/diffusion_pytorch_model.safetensors",
    # Native turbo multi-view texture model.
    "hunyuan3d-paint-v2-0-turbo/feature_extractor/*",
    "hunyuan3d-paint-v2-0-turbo/image_encoder/config.json",
    "hunyuan3d-paint-v2-0-turbo/image_encoder/model.safetensors",
    "hunyuan3d-paint-v2-0-turbo/image_encoder/preprocessor_config.json",
    "hunyuan3d-paint-v2-0-turbo/model_index.json",
    "hunyuan3d-paint-v2-0-turbo/scheduler/*",
    "hunyuan3d-paint-v2-0-turbo/text_encoder/config.json",
    "hunyuan3d-paint-v2-0-turbo/text_encoder/pytorch_model.bin",
    "hunyuan3d-paint-v2-0-turbo/tokenizer/*",
    "hunyuan3d-paint-v2-0-turbo/unet/config.json",
    "hunyuan3d-paint-v2-0-turbo/unet/diffusion_pytorch_model.safetensors",
    "hunyuan3d-paint-v2-0-turbo/unet/modules.py",
    "hunyuan3d-paint-v2-0-turbo/vae/config.json",
    "hunyuan3d-paint-v2-0-turbo/vae/diffusion_pytorch_model.bin",
]


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    result = snapshot_download(
        repo_id=REPO_ID,
        local_dir=TARGET,
        allow_patterns=ALLOW_PATTERNS,
        max_workers=4,
    )
    print(f"Paint weights ready: {result}")


if __name__ == "__main__":
    main()
