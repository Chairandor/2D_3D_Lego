"""Initialize the native pyldraw colour module without altering shared resources."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ldraw", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = args.ldraw.resolve() / "LDConfig.ldr"
    if not source.is_file():
        raise FileNotFoundError(source)
    # A palette-only catalog needs no geometry index or generated part classes.
    shadow = root / ".runtime/palette-source"
    shadow.mkdir(parents=True, exist_ok=True)
    (shadow / "parts.lst").touch()
    link = shadow / "LDConfig.ldr"
    if not link.exists():
        link.symlink_to(source)
    elif link.resolve() != source:
        raise ValueError("Palette source differs from the configured library")
    from ldraw.config import Config
    from ldraw.generation.colours import gen_colours
    from ldraw.parts import Parts
    generated = root / ".runtime/data/pyldraw3/generated"
    library = generated / "library"
    library.mkdir(parents=True, exist_ok=True)
    (library / "__init__.py").touch()
    Config(ldraw_library_path=str(args.ldraw.resolve()), generated_path=str(generated)).write(
        root / ".runtime/config/pyldraw3/config.yml")
    gen_colours(Parts(shadow / "parts.lst"), library)
    print("Native palette generated from", source)


if __name__ == "__main__":
    main()
