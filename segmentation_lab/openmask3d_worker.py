"""Run the installed OpenMask3D mask head and export a portable, verified mask array."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import os
from pathlib import Path
import subprocess
import sys

import numpy as np


def export_masks(scene: Path, trusted_masks: Path, output: Path) -> None:
    """Read a trusted server-side mask result and bind it to the original PLY."""
    import torch

    options = {"map_location": "cpu"}
    if "weights_only" in inspect.signature(torch.load).parameters:
        options["weights_only"] = False  # Upstream saves a NumPy array. Only the local worker's trusted result is read.
    values = torch.load(str(trusted_masks), **options)
    if isinstance(values, torch.Tensor):
        values = values.detach().numpy()
    if not isinstance(values, np.ndarray) or values.ndim != 2 or values.dtype.kind not in "bifu":
        raise ValueError("OpenMask3D did not produce a scene-point by instance matrix.")
    count = 0
    with scene.open("rb") as stream:
        header = stream.read(16384).split(b"end_header", 1)
        if len(header) != 2:
            raise ValueError("Missing PLY header.")
        for line in header[0].splitlines():
            if line.startswith(b"element vertex "):
                count = int(line.split()[2])
                break
    if count <= 0 or count != values.shape[0] or values.shape[1] == 0:
        raise ValueError(f"Mask shape {values.shape} does not align to {count} scene vertices.")
    for start in range(0, count, 4096):
        block = values[start:start + 4096]
        if not np.all((block == 0) | (block == 1)):
            raise ValueError("OpenMask3D mask results must be binary.")
    digest = hashlib.sha256()
    with scene.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    np.savez_compressed(output, masks=values.astype(bool), scene_sha256=digest.hexdigest())
    print(f"Exported {values.shape[1]} masks aligned to {count} points: {output}", flush=True)


def mask_cosine_scores(features: np.ndarray, embedding: np.ndarray) -> list[float | None]:
    """Score each saved mask with the same cosine similarity as OpenMask3D."""
    if features.ndim != 2 or not len(features) or not np.isfinite(features).all():
        raise ValueError("OpenMask3D mask features must be a finite K×D array.")
    if embedding.shape != (features.shape[1],):
        raise ValueError("Saved mask features and the CLIP text model have different embedding sizes.")
    query_norm = np.linalg.norm(embedding)
    if not np.isfinite(query_norm) or query_norm == 0:
        raise ValueError("Invalid CLIP text embedding.")
    norms = np.linalg.norm(features, axis=1)
    valid = norms >= 0.001
    scores = np.zeros(len(features), dtype=np.float32)
    scores[valid] = np.clip((features[valid] @ (embedding / query_norm)) / norms[valid], -1, 1)
    return [float(score) if available else None for score, available in zip(scores, valid)]


def score_query(features_file: Path, clip_weights: Path, query: str) -> list[float | None]:
    """Encode the query with the installed CLIP weights for these mask features."""
    import clip
    import torch

    features = np.load(features_file, allow_pickle=False).astype(np.float32)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, _ = clip.load(str(clip_weights.resolve(strict=True)), device=device)
    with torch.inference_mode():
        embedding = model.encode_text(clip.tokenize(query).to(device)).float().cpu().numpy()[0]
    return mask_cosine_scores(features, embedding)


def main() -> None:
    import json

    if sys.argv[1:2] == ["query"]:
        parser = argparse.ArgumentParser(description="Score saved OpenMask3D features for a text prompt.")
        parser.add_argument("features", type=Path)
        parser.add_argument("clip_weights", type=Path)
        parser.add_argument("text")
        args = parser.parse_args(sys.argv[2:])
        print(json.dumps(score_query(args.features, args.clip_weights, args.text)), flush=True)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--gpu")
    parser.add_argument("--saved-mask", type=Path, help="Export an existing trusted server-side result.")
    args = parser.parse_args()
    scene, output = args.scene.resolve(strict=True), args.output.resolve()
    if output.exists() or output.suffix.lower() != ".npz":
        parser.error("Output must be a new .npz file.")
    if args.saved_mask:
        export_masks(scene, args.saved_mask.resolve(strict=True), output)
        return
    if not args.project or not args.checkpoint:
        parser.error("Running OpenMask3D requires --project and --checkpoint.")
    project = args.project.resolve(strict=True) / "openmask3d"
    script = project / "class_agnostic_mask_computation/get_masks_single_scene.py"
    if not script.is_file():
        parser.error(f"Missing inference entry point: {script}")
    checkpoint = args.checkpoint.resolve(strict=True)
    generated = output.parent / "openmask3d"
    generated.mkdir()
    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "3")
    if args.gpu is not None:
        env["CUDA_VISIBLE_DEVICES"] = args.gpu
    subprocess.run([sys.executable, str(script), "general.experiment_name=legolizer_ui",
                    f"general.checkpoint={checkpoint}", "general.train_mode=false", "data.test_mode=test",
                    "model.num_queries=120", "general.use_dbscan=true", "general.dbscan_eps=0.95",
                    "general.save_visualizations=false", f"general.scene_path={scene}",
                    f"general.mask_save_dir={generated}", f"hydra.run.dir={generated / 'hydra'}"],
                   cwd=project, env=env, check=True, timeout=900)
    export_masks(scene, generated / (scene.stem + "_masks.pt"), output)


if __name__ == "__main__":
    main()
