"""Opt-in source-frame orientation for meshes with known capture conventions.

The SF3D preset uses +Y as source up and -Z as the input camera side. Broad,
orthogonal planar faces may correct small pitch/roll/yaw errors; otherwise the
declared source frame wins. This is display alignment, not semantic recognition
or a physical stability certificate.
"""

from __future__ import annotations

import numpy as np
import open3d as o3d

from legolizer.processing3d.orientation import _validated_arrays


_SOURCE_UP = np.array([0.0, 1.0, 0.0])
_SOURCE_FRONT = np.array([0.0, 0.0, -1.0])
_MIN_AREA_SHARE = 0.12
_CLUSTER_ANGLE_DEG = 12.0
_MAX_UP_CORRECTION_DEG = 25.0
_MAX_FRONT_CORRECTION_DEG = 35.0


def _angle_degrees(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(first @ second, -1.0, 1.0))))


def _planar_directions(mesh: o3d.geometry.TriangleMesh) -> list[tuple[np.ndarray, float]]:
    """Find dominant unoriented face-normal clusters with scale-free area shares."""
    vertices, triangles = _validated_arrays(mesh)
    faces = vertices[triangles]
    cross = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    scale = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    valid = lengths > np.finfo(np.float64).eps * scale * scale * 64.0
    if not np.any(valid):
        return []
    normals = cross[valid] / lengths[valid, None]
    areas = lengths[valid]
    total = float(areas.sum())
    available = np.ones(len(areas), dtype=bool)
    cosine = float(np.cos(np.radians(_CLUSTER_ANGLE_DEG)))
    found: list[tuple[np.ndarray, float]] = []
    for _ in range(3):
        remaining = np.flatnonzero(available)
        if not len(remaining):
            break
        seed = remaining[int(np.argmax(areas[remaining]))]
        dots = normals @ normals[seed]
        members = available & (np.abs(dots) >= cosine)
        share = float(areas[members].sum() / total)
        if share < _MIN_AREA_SHARE:
            break
        direction = np.sum(
            normals[members] * (areas[members] * np.sign(dots[members]))[:, None],
            axis=0,
        )
        direction /= np.linalg.norm(direction)
        found.append((direction, share))
        available[members] = False
    return found


def source_frame_rotation(
    mesh: o3d.geometry.TriangleMesh, preset: str = "sf3d"
) -> tuple[np.ndarray, dict[str, object]]:
    """Return a proper 4×4 source-to-Z-up rotation and auditable evidence.

    Input coordinates are unit-agnostic. The rotation does not move the mesh
    to the ground; the normal pipeline normalization handles translation.
    """
    if preset != "sf3d":
        raise ValueError("source_frame 目前只支持 sf3d")
    _validated_arrays(mesh)
    directions = _planar_directions(mesh)
    up = _SOURCE_UP.copy()
    front = _SOURCE_FRONT.copy()
    status = "source_frame_only"
    up_error = None
    front_error = None
    up_share = None
    front_share = None
    if len(directions) >= 2:
        up_candidates = [(index, direction if direction @ up >= 0 else -direction,
                          share) for index, (direction, share) in enumerate(directions)]
        up_index, found_up, up_share = min(
            up_candidates, key=lambda item: _angle_degrees(item[1], up)
        )
        front_candidates = [
            (direction if direction @ front >= 0 else -direction, share)
            for index, (direction, share) in enumerate(directions) if index != up_index
        ]
        found_front, front_share = min(
            front_candidates, key=lambda item: _angle_degrees(item[0], front)
        )
        up_error = _angle_degrees(found_up, up)
        front_error = _angle_degrees(found_front, front)
        if (
            up_error <= _MAX_UP_CORRECTION_DEG
            and front_error <= _MAX_FRONT_CORRECTION_DEG
            and abs(float(found_up @ found_front)) <= 0.15
        ):
            up = found_up
            front = found_front - float(found_front @ up) * up
            front /= np.linalg.norm(front)
            status = "planar_correction"
    right = np.cross(up, front)
    right /= np.linalg.norm(right)
    rotation = np.stack((right, -front, up))
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    report: dict[str, object] = {
        "preset": preset,
        "selection_source": "source_frame",
        "selected_axis": "y",
        "confidence": None,
        "ambiguous": False,
        "candidates": [],
        "reason": "已知 SF3D 源坐标系；大平面仅用于小角度校正，不代表语义或稳定性判断",
        "source_up_vector": _SOURCE_UP.tolist(),
        "source_front_vector": _SOURCE_FRONT.tolist(),
        "target_front_vector": [0.0, -1.0, 0.0],
        "alignment_status": status,
        "planar_up_deviation_degrees": up_error,
        "planar_front_deviation_degrees": front_error,
        "planar_up_area_share": up_share,
        "planar_front_area_share": front_share,
        "planar_min_area_share": _MIN_AREA_SHARE,
        "max_up_correction_degrees": _MAX_UP_CORRECTION_DEG,
        "max_front_correction_degrees": _MAX_FRONT_CORRECTION_DEG,
    }
    return transform, report
