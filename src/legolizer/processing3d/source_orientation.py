"""Standalone copy of the generic SF3D mesh orientation stage.

This module is extracted from ``pipeline_2d_to_lego.py`` so it can be shared
without the rest of the 2D-to-LEGO pipeline.  It does not use object names,
colours, or chicken-specific landmarks.
"""

from __future__ import annotations

import argparse
from itertools import permutations, product
from pathlib import Path

import numpy as np
import trimesh


def _cube_rotations() -> list[np.ndarray]:
    """Return the 24 proper right-angle rotations (no reflections)."""
    matrices: list[np.ndarray] = []
    for permutation in permutations(range(3)):
        for signs in product((-1.0, 1.0), repeat=3):
            matrix = np.zeros((3, 3))
            for row, axis in enumerate(permutation):
                matrix[row, axis] = signs[row]
            if abs(float(np.linalg.det(matrix)) - 1.0) < 1e-6:
                matrices.append(matrix)
    return matrices


def _upright_score(mesh: trimesh.Trimesh) -> float:
    """Prefer a compact ground contact without object-specific colour cues."""
    vertices = np.asarray(mesh.vertices)
    ymin = float(vertices[:, 1].min())
    ymax = float(vertices[:, 1].max())
    span = max(ymax - ymin, 1e-6)
    bottom = vertices[:, 1] <= ymin + 0.08 * span
    support = vertices[bottom][:, [0, 2]]
    support_area = (
        float(np.ptp(support[:, 0]) * np.ptp(support[:, 1])) if bottom.any() else 9e9
    )
    centre_height = (float(vertices[:, 1].mean()) - ymin) / span
    return 3.0 * centre_height - 6.0 * support_area


def _input_view_score(rotation: np.ndarray, face_rotation: np.ndarray) -> float:
    """Keep SF3D's input-image viewing side and screen-right after snapping."""
    transform = rotation @ face_rotation
    front = (transform @ np.array([0.0, 0.0, -1.0]))[[0, 2]]
    right = (transform @ np.array([-1.0, 0.0, 0.0]))[[0, 2]]
    front /= max(float(np.linalg.norm(front)), 1e-9)
    right /= max(float(np.linalg.norm(right)), 1e-9)
    return float(
        front @ np.array([0.0, -1.0])
        + 0.35 * right @ np.array([-1.0, 0.0])
    )


def _face_axis_rotation(mesh: trimesh.Trimesh) -> np.ndarray:
    """Return a transform mapping dominant face-normal axes onto XYZ."""
    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces, dtype=np.float64)
    remaining = np.ones(len(normals), dtype=bool)
    axes: list[np.ndarray] = []
    cosine = float(np.cos(np.radians(25.0)))

    for _ in range(3):
        indices = np.flatnonzero(remaining)
        if len(indices) == 0:
            break
        seed = indices[int(np.argmax(weights[indices]))]
        direction = normals[seed]
        clustered = remaining & (np.abs(normals @ direction) >= cosine)
        signs = np.sign(normals[clustered] @ direction)
        signs[signs == 0] = 1
        mean = (
            normals[clustered] * (weights[clustered] * signs)[:, None]
        ).sum(axis=0)
        norm = float(np.linalg.norm(mean))
        if norm < 1e-9:
            break
        axes.append(mean / norm)
        remaining &= ~clustered

    if len(axes) < 3:
        covariance = np.zeros((3, 3), dtype=np.float64)
        for normal, weight in zip(normals, weights):
            covariance += weight * np.outer(normal, normal)
        _values, eigenvectors = np.linalg.eigh(covariance)
        rotation = np.asarray(eigenvectors, dtype=np.float64)
    else:
        rotation = np.column_stack(axes)
        rotation, _remainder = np.linalg.qr(rotation)

    if float(np.linalg.det(rotation)) < 0:
        rotation[:, 0] *= -1
    transform = np.eye(4)
    transform[:3, :3] = rotation.T
    return transform


def _level_to_ground(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Remove residual pitch/roll and snap horizontal sides to X/Z."""
    from trimesh.geometry import align_vectors
    from trimesh.transformations import rotation_matrix

    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces, dtype=np.float64)
    downward = normals[:, 1] < -0.5
    if downward.any():
        mean = (normals[downward] * weights[downward, None]).sum(axis=0)
        mean /= np.linalg.norm(mean)
        mesh.apply_transform(align_vectors(mean, np.array([0.0, -1.0, 0.0])))

    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces, dtype=np.float64)
    side = np.abs(normals[:, 1]) < 0.35
    if side.any():
        xz = normals[side][:, [0, 2]]
        angles = np.arctan2(xz[:, 1], xz[:, 0])
        folded = (angles + np.pi / 4.0) % (np.pi / 2.0) - np.pi / 4.0
        yaw = -float(np.average(folded, weights=weights[side]))
        if abs(yaw) > np.radians(0.5):
            mesh.apply_transform(rotation_matrix(yaw, [0.0, 1.0, 0.0]))
    return mesh


def _point_in_convex(polygon: np.ndarray, point: np.ndarray) -> bool:
    if len(polygon) < 3:
        return False
    try:
        from scipy.spatial import ConvexHull

        vertices = polygon[ConvexHull(polygon).vertices]
    except Exception:
        vertices = polygon
    shifted = np.vstack([vertices, vertices[:1]])
    edge = shifted[1:] - shifted[:-1]
    to_point = np.asarray(point, dtype=np.float64) - shifted[:-1]
    crosses = edge[:, 0] * to_point[:, 1] - edge[:, 1] * to_point[:, 0]
    return bool(np.all(crosses >= -1e-7) or np.all(crosses <= 1e-7))


def report_stance(mesh: trimesh.Trimesh) -> None:
    """Print floor tilt, grid alignment, and a static-stability diagnostic."""
    normals = np.asarray(mesh.face_normals)
    weights = np.asarray(mesh.area_faces)
    nearest = np.abs(normals).max(axis=1)
    off_axis = np.degrees(np.arccos(np.clip(nearest, 0.0, 1.0)))
    downward = normals[:, 1] < -0.5
    tilt = 0.0
    if downward.any():
        mean = (normals[downward] * weights[downward, None]).sum(axis=0)
        mean /= np.linalg.norm(mean)
        tilt = float(np.degrees(np.arccos(np.clip(-mean[1], -1.0, 1.0))))
    vertices = np.asarray(mesh.vertices)
    ymin = float(vertices[:, 1].min())
    span = max(float(np.ptp(vertices[:, 1])), 1e-6)
    bottom = vertices[:, 1] <= ymin + 0.05 * span
    support = vertices[bottom][:, [0, 2]]
    centre_of_mass = np.asarray(mesh.center_mass)
    stable = (
        _point_in_convex(support, centre_of_mass[[0, 2]]) if bottom.any() else False
    )
    print(
        f"stance: floor tilt {tilt:.1f}deg, "
        f"faces off-axis median {float(np.median(off_axis)):.1f}deg, "
        f"COM over feet={stable}",
        flush=True,
    )


def upright_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Axis-align an SF3D reconstruction while retaining its source view."""
    face_transform = _face_axis_rotation(mesh)
    mesh.apply_transform(face_transform)
    face_rotation = face_transform[:3, :3]
    best_mesh: trimesh.Trimesh | None = None
    best_score = float("-inf")
    best_view_score = float("-inf")

    for rotation in _cube_rotations():
        transform = np.eye(4)
        transform[:3, :3] = rotation
        candidate = mesh.copy()
        candidate.apply_transform(transform)
        source_up = rotation @ face_rotation @ np.array([0.0, 1.0, 0.0])
        view_score = _input_view_score(rotation, face_rotation)
        score = 100.0 * float(source_up[1]) + _upright_score(candidate)
        score += 2.0 * view_score
        if score > best_score:
            best_score = score
            best_view_score = view_score
            best_mesh = candidate

    assert best_mesh is not None
    best_mesh = _level_to_ground(best_mesh)
    ymin = float(best_mesh.bounds[0][1])
    best_mesh.apply_translation([0.0, -ymin, 0.0])
    print(
        f"upright mesh extents {np.round(best_mesh.extents, 4).tolist()} "
        f"(Y-up, seated on Y=0, score={best_score:.1f}, "
        f"input-view alignment={best_view_score:.2f})",
        flush=True,
    )
    report_stance(best_mesh)
    return best_mesh


def load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force=None, process=False)
    if isinstance(loaded, trimesh.Scene):
        geometries = [
            geometry
            for geometry in loaded.geometry.values()
            if isinstance(geometry, trimesh.Trimesh)
        ]
        if not geometries:
            raise ValueError(f"No triangle mesh found in {path}")
        return trimesh.util.concatenate(geometries)
    if isinstance(loaded, trimesh.Trimesh):
        return loaded
    raise TypeError(f"Unsupported model type: {type(loaded)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input GLB/OBJ/PLY mesh")
    parser.add_argument("output", type=Path, help="output mesh path")
    arguments = parser.parse_args()

    mesh = upright_mesh(load_mesh(arguments.input))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(arguments.output)
    print(f"saved: {arguments.output.resolve()}")


if __name__ == "__main__":
    main()
