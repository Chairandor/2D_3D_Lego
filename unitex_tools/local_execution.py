"""Run the existing UniTEX adapter directly on a Linux GPU host.

Models/environments are reused read-only. All projection caches and overrides
belong to the current run, including when editing an accepted six-view sheet.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def execute(reference: Path, shape: Path, output_dir: Path, *, root: Path,
            remote_root: str, steps: int, seed: int, projection: str,
            gpu_index: int, phase: str, force: bool, run,
            six_view_override: Path | None = None,
            cache_run_dir: Path | None = None) -> Path | None:
    if phase not in {"stage1", "project"}:
        raise ValueError("Unknown UniTEX phase")
    resources = Path(remote_root).expanduser().resolve()
    python = resources / "envs/unitex/bin/python"
    repo = resources / "UniTEX"
    wrapper = root / "unitex_tools/run_unitex_external.py"
    for path in (python, repo, wrapper, reference, shape):
        if not path.exists():
            raise FileNotFoundError(f"UniTEX local resource missing: {path}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = output_dir / "cache"
    required = ("camera_info.pth", "processed_mesh.obj", "mv_rgb.png")

    def complete(directory: Path) -> bool:
        return all((directory / name).is_file() for name in required)

    env = {
        "CUDA_VISIBLE_DEVICES": str(int(gpu_index)),
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
        "HF_HOME": str(resources / "cache/huggingface"),
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "DIFFUSERS_OFFLINE": "1",
        "UNITEX_RAY_BACKEND": "open3d", "UNITEX_FLUX_MODEL": str(resources / "models/FLUX.1-dev"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def invoke(requested: str) -> None:
        run([str(python), str(wrapper), "--repo", str(repo), "--image", str(reference.resolve()),
             "--mesh", str(shape.resolve()), "--output", str(output_dir), "--phase", requested,
             "--projection", projection, "--steps", str(steps), "--seed", str(seed),
             "--reference-mode", "rgba"], cwd=root, env=env)

    if phase == "project" and not complete(cache):
        source = cache_run_dir.resolve() / "unitex/cache" if cache_run_dir is not None else None
        if source and source.resolve() != cache.resolve() and complete(source):
            shutil.copytree(source, cache, dirs_exist_ok=True)
            print("reuse original UniTEX cache in a separate local branch", flush=True)
        else:
            print("local UniTEX cache missing; rebuilding stage-1", flush=True)
            invoke("stage1")
        if not complete(cache):
            raise RuntimeError("UniTEX stage-1 did not produce a complete projection cache")

    if phase == "project" and six_view_override is not None:
        override = six_view_override.resolve()
        if not override.is_file() or override.suffix.lower() != ".png":
            raise ValueError("UniTEX six-view replacement must be an existing PNG")
        for destination in (cache / "mv_rgb.png", output_dir / "mv_rgb.png"):
            if override != destination.resolve():
                shutil.copy2(override, destination)

    marker = output_dir / ("mv_rgb.png" if phase == "stage1" else "textured_mesh.glb")
    if force or six_view_override is not None or not marker.is_file() or (phase == "stage1" and not complete(cache)):
        invoke(phase)
    else:
        print(f"reuse local UniTEX {phase}: {marker}", flush=True)
    if not marker.is_file():
        raise RuntimeError(f"UniTEX {phase} did not produce {marker.name}")
    return marker if phase == "project" else None
