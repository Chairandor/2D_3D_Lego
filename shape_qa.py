"""Object-agnostic 3D shape review against a foreground alpha mask.

This is a diagnostic gate, not an automatic promise that one score can judge
all objects. It compares silhouettes after searching camera angle, reports
topology, and measures local normal variation without changing any mesh.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import trimesh


def normalized_mask(mask: np.ndarray, size: int = 256) -> np.ndarray:
    rows, cols = np.nonzero(mask)
    if not len(rows):
        raise ValueError("empty foreground mask")
    crop = mask[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    scale = 0.82 * size / max(crop.shape)
    width = max(1, int(round(crop.shape[1] * scale)))
    height = max(1, int(round(crop.shape[0] * scale)))
    scaled = cv2.resize(crop.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST)
    result = np.zeros((size, size), dtype=np.uint8)
    x0 = (size - width) // 2
    y0 = (size - height) // 2
    result[y0:y0 + height, x0:x0 + width] = scaled
    return result > 0


def silhouette(mesh: trimesh.Trimesh, yaw: float, elevation: float, size: int = 256) -> np.ndarray:
    centre = np.asarray(mesh.bounds).mean(axis=0)
    yaw = np.deg2rad(yaw)
    elevation = np.deg2rad(elevation)
    camera = np.array([
        np.sin(yaw) * np.cos(elevation),
        np.sin(elevation),
        np.cos(yaw) * np.cos(elevation),
    ])
    forward = -camera
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    points = np.asarray(mesh.vertices) - centre
    xy = np.column_stack((points @ right, points @ up))
    span = np.ptp(xy, axis=0)
    scale = 0.82 * size / max(float(span.max()), 1e-12)
    pixels = np.column_stack((size / 2 + xy[:, 0] * scale, size / 2 - xy[:, 1] * scale))
    triangles = np.rint(pixels[np.asarray(mesh.faces)]).astype(np.int32)
    result = np.zeros((size, size), dtype=np.uint8)
    # OpenCV's multi-contour fill uses even/odd parity: overlapping front and
    # back triangles can cancel each other. Union triangles one by one.
    for triangle in triangles:
        cv2.fillConvexPoly(result, triangle, 1)
    return result > 0


def compare_silhouettes(mesh: trimesh.Trimesh, reference: np.ndarray) -> tuple[dict, np.ndarray]:
    best = {"silhouette_iou": -1.0, "yaw_degrees": 0, "elevation_degrees": 0}
    best_mask = np.zeros_like(reference)
    for elevation in (-15, 0, 15, 30):
        for yaw in range(0, 360, 30):
            candidate = silhouette(mesh, yaw, elevation, size=reference.shape[0])
            intersection = np.count_nonzero(candidate & reference)
            union = np.count_nonzero(candidate | reference)
            score = intersection / max(union, 1)
            if score > best["silhouette_iou"]:
                best = {
                    "silhouette_iou": float(score),
                    "yaw_degrees": yaw,
                    "elevation_degrees": elevation,
                }
                best_mask = candidate
    return best, best_mask


def preview_mesh(mesh: trimesh.Trimesh, max_faces: int = 30000) -> trimesh.Trimesh:
    if len(mesh.faces) <= max_faces:
        return mesh
    import pymeshlab

    scene = pymeshlab.MeshSet()
    scene.add_mesh(pymeshlab.Mesh(
        vertex_matrix=np.asarray(mesh.vertices),
        face_matrix=np.asarray(mesh.faces),
    ))
    scene.meshing_decimation_quadric_edge_collapse(targetfacenum=max_faces, preservetopology=True)
    reduced = scene.current_mesh()
    return trimesh.Trimesh(
        vertices=reduced.vertex_matrix(),
        faces=reduced.face_matrix(),
        process=False,
    )


def analyze(mesh: trimesh.Trimesh, image: Image.Image) -> tuple[dict, np.ndarray]:
    rgba = np.asarray(image.convert("RGBA"))
    reference = normalized_mask(rgba[:, :, 3] >= 128)
    reviewed = preview_mesh(mesh)
    silhouette_report, candidate = compare_silhouettes(reviewed, reference)
    angles = np.degrees(np.asarray(reviewed.face_adjacency_angles))
    smooth_edges = angles[angles < 45.0]
    report = {
        "vertices": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
        "preview_faces": int(len(reviewed.faces)),
        "local_normal_angle_p75_degrees": (
            float(np.percentile(smooth_edges, 75)) if len(smooth_edges) else None
        ),
        **silhouette_report,
    }
    overlay = np.full((*reference.shape, 3), 238, dtype=np.uint8)
    overlay[reference & candidate] = [205, 205, 205]
    overlay[reference & ~candidate] = [80, 165, 95]
    overlay[candidate & ~reference] = [220, 100, 100]
    return report, overlay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mesh", type=Path)
    parser.add_argument("foreground", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    mesh = trimesh.load(args.mesh, force="mesh")
    with Image.open(args.foreground) as image:
        report, overlay = analyze(mesh, image)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "shape_qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    Image.fromarray(overlay).resize((768, 768), Image.Resampling.NEAREST).save(
        args.output_dir / "shape_qa_overlay.png"
    )
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
