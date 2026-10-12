"""Prepare an image for Paint3D's IP-Adapter without object-specific rules."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--background",
        default="255,255,255",
        help="RGB matte used only where the source is transparent (default: white)",
    )
    args = parser.parse_args()

    rgb = tuple(int(value) for value in args.background.split(","))
    if len(rgb) != 3 or any(value < 0 or value > 255 for value in rgb):
        raise ValueError("--background must contain three values from 0 to 255")

    source = Image.open(args.source)
    if "A" in source.getbands():
        rgba = source.convert("RGBA")
        matte = Image.new("RGBA", rgba.size, (*rgb, 255))
        prepared = Image.alpha_composite(matte, rgba).convert("RGB")
    else:
        prepared = source.convert("RGB")

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    prepared.save(args.destination, quality=95)
    print(f"saved {args.destination} ({prepared.width}x{prepared.height}, {prepared.mode})")


if __name__ == "__main__":
    main()
