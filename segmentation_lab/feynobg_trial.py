"""Run the FeyNobg background-matting candidate without modifying upstream repos.

This script is intentionally generic: it accepts any raster image and writes a
soft alpha matte, an RGBA cutout, and a small JSON report.  It can reuse an
existing CUDA PyTorch runtime while loading ``nobg`` from the isolated
``nobg_env`` next to this file.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--model", default="feyninc/FeyNobg")
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument(
        "--nobg-site-packages",
        type=Path,
        default=Path(__file__).parent / "nobg_env" / "Lib" / "site-packages",
    )
    return parser.parse_args()


def load_nobg(site_packages: Path):
    # Load the caller's PyTorch/torchvision first.  In this project that lets us
    # reuse the tested SF3D CUDA stack while keeping nobg itself isolated.
    import torch

    try:
        import torchvision  # noqa: F401
    except ImportError:
        pass

    site_text = str(site_packages.resolve())
    if site_text not in sys.path:
        sys.path.insert(0, site_text)

    # The isolated directory also contains CPU-wheel metadata.  Packages such
    # as Accelerate must inspect the versions of the modules already loaded
    # from the CUDA runtime, otherwise NumPy 1.x can be mistaken for 2.x.
    runtime_versions = {
        "numpy": np.__version__,
        "torch": torch.__version__,
    }
    real_version = importlib.metadata.version

    def runtime_aware_version(distribution_name: str) -> str:
        loaded = runtime_versions.get(distribution_name.lower())
        return loaded if loaded is not None else real_version(distribution_name)

    importlib.metadata.version = runtime_aware_version

    # Transformers probes optional audio packages during an image-only import.
    # The SF3D environment contains an obsolete torchaudio binary, so advertise
    # it as unavailable instead of importing a dependency this task never uses.
    import transformers.utils.import_utils as import_utils

    import_utils.is_torchaudio_available = lambda: False

    from nobg import AutoModel, AutoProcessor, BiRefNet, BiRefNetImageProcessor

    return torch, AutoModel, AutoProcessor, BiRefNet, BiRefNetImageProcessor


def alpha_to_u8(alpha) -> np.ndarray:
    if hasattr(alpha, "detach"):
        alpha = alpha.detach().float().cpu().numpy()
    array = np.asarray(alpha, dtype=np.float32).squeeze()
    if array.ndim != 2:
        raise ValueError(f"expected a 2D alpha matte, got {array.shape}")
    if float(array.max(initial=0.0)) <= 1.0 + 1e-6:
        array = array * 255.0
    return np.clip(np.rint(array), 0, 255).astype(np.uint8)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch, AutoModel, AutoProcessor, BiRefNet, BiRefNetImageProcessor = load_nobg(
        args.nobg_site_packages
    )

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")

    started = time.perf_counter()
    model_path = Path(args.model)
    if model_path.exists():
        model = BiRefNet.from_pretrained(model_path).eval().to(device)
        processor = BiRefNetImageProcessor.from_pretrained(model_path)
    else:
        model = AutoModel.from_pretrained(args.model).eval().to(device)
        processor = AutoProcessor.from_pretrained(args.model)
    image = Image.open(args.input).convert("RGB")
    inputs = processor(image, return_tensors="pt")
    pixel_values = inputs["pixel_values"].to(device)

    with torch.inference_mode():
        outputs = model(pixel_values=pixel_values)
    alpha = processor.post_process_alpha_matting(
        outputs, target_sizes=[(image.height, image.width)]
    )[0]

    alpha_u8 = alpha_to_u8(alpha)
    alpha_image = Image.fromarray(alpha_u8, mode="L")
    cutout = image.copy()
    cutout.putalpha(alpha_image)

    alpha_path = args.output_dir / "alpha.png"
    cutout_path = args.output_dir / "cutout.png"
    report_path = args.output_dir / "report.json"
    alpha_image.save(alpha_path)
    cutout.save(cutout_path)

    report = {
        "schema": "feynobg-trial/v1",
        "input": str(args.input.resolve()),
        "model": args.model,
        "device": device,
        "torch": torch.__version__,
        "image_size": [image.width, image.height],
        "alpha": {
            "min": int(alpha_u8.min()),
            "max": int(alpha_u8.max()),
            "mean": float(alpha_u8.mean()),
            "foreground_pixels_at_128": int((alpha_u8 >= 128).sum()),
        },
        "elapsed_seconds": time.perf_counter() - started,
        "outputs": {"alpha": str(alpha_path), "cutout": str(cutout_path)},
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
