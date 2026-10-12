"""Run upstream SF3D on an already-transparent PNG without importing rembg.

The upstream script eagerly imports rembg and initializes a session even when
remove_background() will skip it for an RGBA input. This wrapper refuses opaque
images, then supplies a defensive stub so no second background-removal model
can run. Upstream SF3D source remains untouched.
"""

from __future__ import annotations

import argparse
import os
import runpy
import sys
import types
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--texture-resolution", type=int, default=512)
    args = parser.parse_args()
    image_path = args.image.resolve()
    with Image.open(image_path) as image:
        if image.mode != "RGBA" or image.getextrema()[3][0] >= 255:
            parser.error("This runner requires a genuinely transparent RGBA image")

    stub = types.ModuleType("rembg")
    stub.new_session = lambda: None

    def forbidden_remove(*_args, **_kwargs):
        raise RuntimeError("rembg should not be called for an RGBA image")

    stub.remove = forbidden_remove
    sys.modules["rembg"] = stub

    sf3d_root = Path(__file__).resolve().parent.parent / "stable-fast-3d"
    sys.path.insert(0, str(sf3d_root))
    os.chdir(sf3d_root)
    sys.argv = [
        str(sf3d_root / "run.py"),
        str(image_path),
        "--output-dir", str(args.output_dir.resolve()),
        "--texture-resolution", str(args.texture_resolution),
        "--remesh_option", "none",
    ]
    runpy.run_path(str(sf3d_root / "run.py"), run_name="__main__")


if __name__ == "__main__":
    main()
