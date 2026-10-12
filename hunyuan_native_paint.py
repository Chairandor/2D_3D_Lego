"""Repository-external, object-agnostic Hunyuan3D native texture stage.

This wrapper keeps the upstream checkout untouched.  It uses Hunyuan's own
delighting, six-view diffusion, UV baking, and inpainting while loading the two
diffusion pipelines on CPU first so an 8 GB CUDA GPU can use model offload.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pymeshlab
import torch  # Load torch DLLs before the custom rasterizer extension.
import trimesh
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "Hunyuan3D-2"
# Keep all Hugging Face/Diffusers generated modules on the writable project
# drive.  Diffusers otherwise defaults to the user's C: profile even when all
# model weights are local.
HF_CACHE = ROOT / "models" / "hf-cache"
os.environ.setdefault("HF_HOME", str(HF_CACHE))
os.environ.setdefault("HF_MODULES_CACHE", str(HF_CACHE / "modules"))
sys.path.insert(0, str(UPSTREAM))

from hy3dgen.texgen.pipelines import (  # noqa: E402
    Hunyuan3DPaintPipeline,
    Hunyuan3DTexGenConfig,
)
from hy3dgen.texgen.utils.uv_warp_utils import mesh_uv_wrap  # noqa: E402
from render_refinement_comparison import render  # noqa: E402


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def decimate(mesh: trimesh.Trimesh, max_faces: int) -> trimesh.Trimesh:
    if len(mesh.faces) <= max_faces:
        return mesh.copy()
    scene = pymeshlab.MeshSet()
    scene.add_mesh(
        pymeshlab.Mesh(
            vertex_matrix=np.asarray(mesh.vertices),
            face_matrix=np.asarray(mesh.faces),
        )
    )
    scene.meshing_decimation_quadric_edge_collapse(
        targetfacenum=max_faces,
        preservetopology=True,
        preservenormal=True,
    )
    reduced = scene.current_mesh()
    result = trimesh.Trimesh(
        vertices=reduced.vertex_matrix(),
        faces=reduced.face_matrix(),
        process=True,
    )
    trimesh.repair.fix_normals(result)
    return result


def textured_previews(mesh: trimesh.Trimesh, output_dir: Path) -> list[str]:
    preview_mesh = mesh.copy()
    if getattr(preview_mesh.visual, "kind", None) == "texture":
        preview_mesh.visual = preview_mesh.visual.to_color()
    cameras = {
        "front": np.asarray((0.0, 1.0, -3.0)),
        "iso": np.asarray((1.6, 1.1, -2.2)),
        "side": np.asarray((3.0, 1.0, 0.0)),
        "back": np.asarray((0.0, 1.0, 3.0)),
    }
    paths: list[str] = []
    for name, camera in cameras.items():
        path = output_dir / f"preview_{name}.png"
        render(preview_mesh, size=(900, 900), camera=camera).save(path)
        paths.append(str(path.resolve()))

    panel = Image.new("RGB", (1800, 1800), (234, 237, 240))
    draw = ImageDraw.Draw(panel)
    for index, name in enumerate(cameras):
        image = Image.open(output_dir / f"preview_{name}.png").convert("RGB")
        x = (index % 2) * 900
        y = (index // 2) * 900
        panel.paste(image, (x, y))
        draw.text((x + 24, y + 20), name.upper(), fill=(20, 20, 20))
    sheet = output_dir / "preview_contact_sheet.png"
    panel.save(sheet)
    paths.append(str(sheet.resolve()))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("foreground", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=ROOT / "models" / "weights" / "Hunyuan3D-2-paint",
    )
    parser.add_argument("--max-faces", type=int, default=120_000)
    parser.add_argument("--resolution", type=int, default=1024)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    source = trimesh.load(args.mesh, force="mesh")
    if not isinstance(source, trimesh.Trimesh):
        raise TypeError(f"Expected one mesh, got {type(source).__name__}")
    log(f"loaded source mesh: {len(source.vertices)} vertices, {len(source.faces)} faces")
    mesh = decimate(source, args.max_faces)
    working_stats = {
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
    }
    working_mesh = args.output_dir / "mesh_paint_working.glb"
    mesh.export(working_mesh)
    log(f"working copy: {len(mesh.vertices)} vertices, {len(mesh.faces)} faces")

    reference = Image.open(args.foreground).convert("RGBA")
    reference.save(args.output_dir / "input_foreground.png")
    alpha = np.asarray(reference)[:, :, 3]
    if not np.any(alpha):
        raise ValueError("Foreground image has an empty alpha mask")

    delight_dir = args.model_dir / "hunyuan3d-delight-v2-0"
    paint_dir = args.model_dir / "hunyuan3d-paint-v2-0-turbo"
    if not delight_dir.is_dir() or not paint_dir.is_dir():
        raise FileNotFoundError(f"Incomplete Paint model directory: {args.model_dir}")

    config = Hunyuan3DTexGenConfig(
        str(delight_dir), str(paint_dir), "hunyuan3d-paint-v2-0-turbo"
    )
    # Load weights into system RAM, then let Accelerate move only the active
    # module to CUDA.  Mesh rasterization itself still uses CUDA.
    config.device = "cpu"
    config.render_size = args.resolution
    config.texture_size = args.resolution
    log("loading native Delight and Turbo Paint models on CPU")
    pipeline = Hunyuan3DPaintPipeline(config)
    pipeline.enable_model_cpu_offload(device="cuda")
    log("model CPU offload enabled")

    recentered = pipeline.recenter_image(reference)
    recentered.save(args.output_dir / "input_recentered.png")
    log("running Hunyuan native delighting")
    delighted = pipeline.models["delight_model"](recentered)
    delighted.save(args.output_dir / "input_delighted.png")

    log("generating UVs and six geometry control views")
    mesh = mesh_uv_wrap(mesh)
    pipeline.render.load_mesh(mesh)
    elevs = pipeline.config.candidate_camera_elevs
    azims = pipeline.config.candidate_camera_azims
    weights = pipeline.config.candidate_view_weights
    normal_maps = pipeline.render_normal_multiview(elevs, azims, use_abs_coor=True)
    position_maps = pipeline.render_position_multiview(elevs, azims)
    camera_info = [
        (((azim // 30) + 9) % 12) // {-20: 1, 0: 1, 20: 1, -90: 3, 90: 3}[elev]
        + {-20: 0, 0: 12, 20: 24, -90: 36, 90: 40}[elev]
        for azim, elev in zip(azims, elevs)
    ]

    log("running Hunyuan native six-view texture diffusion")
    multiviews = pipeline.models["multiview_model"](
        [delighted], normal_maps + position_maps, camera_info
    )
    view_dir = args.output_dir / "generated_views"
    view_dir.mkdir(exist_ok=True)
    for index, view in enumerate(multiviews):
        view.save(view_dir / f"view_{index:02d}.png")
    multiviews = [
        view.resize((args.resolution, args.resolution)) for view in multiviews
    ]

    log("baking generated views into a UV texture")
    texture, mask = pipeline.bake_from_multiview(
        multiviews, elevs, azims, weights, method=pipeline.config.merge_method
    )
    mask_np = (mask.squeeze(-1).cpu().numpy() * 255).astype(np.uint8)
    texture = pipeline.texture_inpaint(texture, mask_np)
    pipeline.render.set_texture(texture)
    textured = pipeline.render.save_mesh()
    output_mesh = args.output_dir / "mesh_textured_native.glb"
    textured.export(output_mesh)
    log(f"exported native textured mesh: {output_mesh}")

    previews = textured_previews(textured, args.output_dir)
    report = {
        "source_mesh": str(args.mesh.resolve()),
        "foreground": str(args.foreground.resolve()),
        "model_dir": str(args.model_dir.resolve()),
        "output_mesh": str(output_mesh.resolve()),
        "source_vertices": int(len(source.vertices)),
        "source_faces": int(len(source.faces)),
        "working_vertices": working_stats["vertices"],
        "working_faces": working_stats["faces"],
        "working_watertight": working_stats["watertight"],
        "uv_vertices": int(len(mesh.vertices)),
        "uv_faces": int(len(mesh.faces)),
        "uv_seams_duplicate_vertices": True,
        "texture_resolution": args.resolution,
        "visual_kind": getattr(textured.visual, "kind", None),
        "previews": previews,
        "elapsed_seconds": round(time.time() - started, 2),
        "cuda_peak_allocated_gb": round(torch.cuda.max_memory_allocated() / 2**30, 3),
    }
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
