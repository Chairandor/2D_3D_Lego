"""Deterministic geometry-only scoring for a mesh's display up axis.

The scores use only the input coordinates and are scale invariant. They do not
infer units or object semantics. ``symmetry_score`` measures sampled surface
reflection symmetry about vertical planes; it is a geometric regularizer, not a
semantic upright detector.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import open3d as o3d


_AXES = ("x", "-x", "y", "-y", "z", "-z")
_AXIS_INDEX = {axis: index for index, axis in enumerate(_AXES)}
_AXIS_COMPONENT = {"x": 0, "y": 1, "z": 2}
_SUPPORT_WEIGHTS = {"contact": 0.65, "balance": 0.35}
# Display posture and unsupported physical placement are different questions.
# Aspect favors a plausible vertical object axis, while the signed stability
# evidence still distinguishes the two ends of that axis.
_DISPLAY_WEIGHTS = {"support": 0.55, "symmetry": 0.30, "verticality": 0.15}
# Heuristic margins audited on the repository's three animal/shape meshes and
# six local Open3D single-object samples; they are not calibrated probabilities.
_AMBIGUITY_GAP = 0.015
_MIN_CONTACT_AREA_RATIO = 0.002
_SLENDER_ASPECT_RATIO = 2.5
_SLENDER_MIN_CONTACT_AREA_RATIO = 0.0005
_MIN_BALANCE_MARGIN = 0.02
_MIN_SIGN_CONTACT_CONTRAST = 0.3
_SYMMETRY_SAMPLES = 2048


def orientation_matrix(axis: str) -> np.ndarray:
    """Return a right-handed rotation mapping the signed source axis to +Z.

    The input mesh is not translated or rescaled. In particular, ``y`` uses
    the project's existing +90 degree rotation about X and ``z`` is identity.
    """
    matrices = {
        "x": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
        "-x": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)),
        "y": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
        "-y": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
        "z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        "-z": ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
    }
    if axis not in matrices:
        raise ValueError("axis 必须是 x、-x、y、-y、z 或 -z")
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = np.asarray(matrices[axis], dtype=np.float64)
    return transform


def score_mesh_orientations(mesh: o3d.geometry.TriangleMesh) -> dict[str, object]:
    """Score all six signed source axes as candidate directions for +Z.

    Near-bottom triangle facets are clipped to a thin slab before their
    projected area is divided by total mesh surface area. This is a geometric
    contact proxy, not the actual mechanical contact area. The balance margin
    is the normalized signed
    distance from the selected center point to the convex support polygon.
    Closed, watertight meshes use a volume centroid; open meshes use a
    labeled surface-area centroid proxy, never claimed as physical mass.
    The symmetry value averages sampled reflection consistency in
    the two vertical planes containing each candidate up axis. The display
    score also rewards a plausible vertical aspect; it is not a claim that
    the mesh can physically stand without a support.

    No coordinate conversion or unit assumption is made. Invalid/empty input
    raises ``ValueError``; geometrically degenerate input returns six tied,
    ambiguous candidates.
    """
    vertices, triangles = _validated_arrays(mesh)
    bounds_min = vertices.min(axis=0)
    bounds_max = vertices.max(axis=0)
    extents = bounds_max - bounds_min
    scale = float(np.linalg.norm(extents))

    triangle_vertices = vertices[triangles]
    cross = np.cross(
        triangle_vertices[:, 1] - triangle_vertices[:, 0],
        triangle_vertices[:, 2] - triangle_vertices[:, 0],
    )
    twice_areas = np.linalg.norm(cross, axis=1)
    area_epsilon = np.finfo(np.float64).eps * scale * scale * 64.0
    valid = np.isfinite(twice_areas) & (twice_areas > area_epsilon)
    if not np.any(valid) or scale <= 0.0 or float(np.max(extents)) <= 0.0:
        return _ambiguous_degenerate_result("网格退化，无法估计支撑方向")

    triangles = triangles[valid]
    triangle_vertices = triangle_vertices[valid]
    areas = twice_areas[valid] * 0.5
    total_surface_area = float(areas.sum())
    if not np.isfinite(total_surface_area) or total_surface_area <= area_epsilon:
        return _ambiguous_degenerate_result("网格表面积退化，无法估计支撑方向")

    if float(np.min(extents)) <= scale * 1e-10:
        return _ambiguous_degenerate_result("网格至少一个方向退化，无法可靠判断上轴")

    center, center_source = _center_of_mass_or_proxy(
        mesh, vertices, triangles, areas, bounds_min, bounds_max, scale
    )
    symmetry = _symmetry_scores(triangle_vertices, areas, bounds_min, bounds_max, scale)
    candidates: list[dict[str, Any]] = []

    for axis in _AXES:
        component = _AXIS_COMPONENT[axis[-1]]
        sign = -1.0 if axis.startswith("-") else 1.0
        height = vertices[:, component] * sign
        minimum_height = float(height.min())
        vertical_extent = float(height.max() - minimum_height)
        contact_tolerance = max(scale * 1e-3, vertical_extent * 1e-2)
        horizontal = [index for index in range(3) if index != component]
        contact_area, support_points = _clipped_bottom_projection(
            triangle_vertices,
            height[triangles],
            minimum_height + contact_tolerance,
            horizontal,
        )
        contact_ratio = float(np.clip(contact_area / total_surface_area, 0.0, 1.0))
        support_hull = _convex_hull_2d(support_points, scale)
        center_projection = center[horizontal]
        margin = _signed_polygon_margin(center_projection, support_hull, scale)
        support_score = float(np.clip(0.5 + 0.5 * margin, 0.0, 1.0))
        symmetry_score = symmetry[component]
        support_proxy_score = (
            _SUPPORT_WEIGHTS["contact"] * contact_ratio
            + _SUPPORT_WEIGHTS["balance"] * support_score
        )
        horizontal_extent = float(max(extents[index] for index in horizontal))
        verticality_score = float(np.clip(vertical_extent / horizontal_extent, 0.0, 1.0))
        score = (
            _DISPLAY_WEIGHTS["support"] * support_proxy_score
            + _DISPLAY_WEIGHTS["symmetry"] * symmetry_score
            + _DISPLAY_WEIGHTS["verticality"] * verticality_score
        )
        candidates.append(
            {
                "axis": axis,
                "score": float(np.clip(score, 0.0, 1.0)),
                "contact_area_ratio": contact_ratio,
                "center_of_mass_margin": float(margin),
                "symmetry_score": float(symmetry_score),
                "center_of_mass_source": center_source,
                "support_score": support_score,
                "support_proxy_score": float(support_proxy_score),
                "stability_score": float(support_proxy_score),
                "verticality_score": verticality_score,
                "contact_area_input_units_squared": contact_area,
                "support_hull_area_input_units_squared": _polygon_area(support_hull),
                "freestanding_assessment": (
                    "geometric_support_proxy_only"
                    if center_source == "closed_mesh_volume_centroid"
                    else "uncertain_open_mesh_surface_centroid"
                ),
            }
        )

    candidates.sort(key=lambda item: (-item["score"], _AXIS_INDEX[item["axis"]]))
    best = candidates[0]
    confidence = float(np.clip(best["score"] - candidates[1]["score"], 0.0, 1.0))
    family_winners = [
        max((item for item in candidates if item["axis"].endswith(component)),
            key=lambda item: item["score"])
        for component in ("x", "y", "z")
    ]
    family_winners.sort(key=lambda item: -item["score"])
    family_confidence = float(family_winners[0]["score"] - family_winners[1]["score"])
    opposite = next(
        item for item in candidates
        if item["axis"].endswith(best["axis"][-1]) and item["axis"] != best["axis"]
    )
    contact_sum = best["contact_area_ratio"] + opposite["contact_area_ratio"]
    sign_contact_contrast = (
        float((best["contact_area_ratio"] - opposite["contact_area_ratio"]) / contact_sum)
        if contact_sum > 0.0 else 0.0
    )
    sorted_extents = sorted((float(value) for value in extents), reverse=True)
    aspect_ratio = sorted_extents[0] / sorted_extents[1]
    slender_exception = (
        aspect_ratio >= _SLENDER_ASPECT_RATIO
        and extents[_AXIS_COMPONENT[best["axis"][-1]]] == sorted_extents[0]
        and best["contact_area_ratio"] >= _SLENDER_MIN_CONTACT_AREA_RATIO
    )
    sufficient_contact = best["contact_area_ratio"] >= _MIN_CONTACT_AREA_RATIO or slender_exception
    balance_threshold = 0.0 if slender_exception else _MIN_BALANCE_MARGIN
    balanced = best["center_of_mass_margin"] >= balance_threshold
    distinct_family = family_confidence >= _AMBIGUITY_GAP
    distinct_sign = sign_contact_contrast >= _MIN_SIGN_CONTACT_CONTRAST
    ambiguous = not (distinct_family and distinct_sign and sufficient_contact and balanced)
    selected_axis = None if ambiguous else str(best["axis"])

    if not sufficient_contact:
        reason = (
            f"最佳候选近底部接触面积比例 {best['contact_area_ratio']:.3f} "
            f"低于最低阈值 {_MIN_CONTACT_AREA_RATIO:.3f}，且不满足细长物体例外，候选方向不可靠"
        )
    elif not distinct_family:
        reason = f"最佳轴族与次佳轴族评分差 {family_confidence:.3f} 小于阈值 {_AMBIGUITY_GAP:.3f}"
    elif not balanced:
        reason = f"最佳候选质心支撑余量 {best['center_of_mass_margin']:.3f} 小于保守门槛 {balance_threshold:.3f}"
    elif not distinct_sign:
        reason = f"同轴正反端接触差异 {sign_contact_contrast:.3f} 不足以区分上下"
    else:
        reason = f"{selected_axis} 的展示轴族和上下端均有几何证据（非正确概率；不代表可独立站立）"

    return {
        "selected_axis": selected_axis,
        "confidence": confidence,
        "confidence_definition": "前两名有符号方向展示分差，仅诊断用；决策分别检查轴族差、正反端接触差和支撑余量，非概率",
        "axis_family": best["axis"][-1],
        "axis_family_confidence": family_confidence,
        "sign_contact_contrast": sign_contact_contrast,
        "ambiguous": bool(ambiguous),
        "candidates": candidates,
        "reason": reason,
        "center_of_mass_source": center_source,
        "score_weights": dict(_DISPLAY_WEIGHTS),
        "stability_score_weights": dict(_SUPPORT_WEIGHTS),
        "support_proxy_score_weights": dict(_SUPPORT_WEIGHTS),
        "contact_definition": "距候选底部不超过 max(0.001*bbox_diagonal, 0.01*height) 的三角片裁剪后水平投影面积之和 / 总网格面积；非真实物理接触面积",
        "ambiguity_gap_threshold": _AMBIGUITY_GAP,
        "minimum_contact_area_ratio": _MIN_CONTACT_AREA_RATIO,
        "minimum_balance_margin": _MIN_BALANCE_MARGIN,
        "minimum_sign_contact_contrast": _MIN_SIGN_CONTACT_CONTRAST,
        "slender_aspect_ratio_threshold": _SLENDER_ASPECT_RATIO,
        "slender_minimum_contact_area_ratio": _SLENDER_MIN_CONTACT_AREA_RATIO,
        "slender_contact_exception_used": bool(slender_exception and best["contact_area_ratio"] < _MIN_CONTACT_AREA_RATIO),
        "symmetry_definition": (
            "面积加权确定性表面采样；关于包含候选上轴的两个垂直平面镜像后，"
            "计算采样点到原三角网格表面的距离，并平均 exp(-distance/(0.03*bbox_diagonal))；不代表语义朝向"
        ),
        "units": "输入坐标单位；未推断或转换单位",
    }


def _validated_arrays(mesh: o3d.geometry.TriangleMesh) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(mesh, o3d.geometry.TriangleMesh):
        raise TypeError("mesh 必须是 open3d.geometry.TriangleMesh")
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    triangles = np.asarray(mesh.triangles, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,) or len(vertices) == 0:
        raise ValueError("mesh 不包含有效顶点")
    if triangles.ndim != 2 or triangles.shape[1:] != (3,) or len(triangles) == 0:
        raise ValueError("mesh 不包含有效三角形")
    if not np.isfinite(vertices).all():
        raise ValueError("mesh 顶点包含 NaN 或无穷坐标")
    if np.any(triangles < 0) or np.any(triangles >= len(vertices)):
        raise ValueError("mesh 三角形索引超出顶点范围")
    return vertices, triangles


def _cross_2d(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return first[..., 0] * second[..., 1] - first[..., 1] * second[..., 0]


def _edge_at_height(
    first: np.ndarray,
    second: np.ndarray,
    first_height: np.ndarray,
    second_height: np.ndarray,
    cutoff: float,
) -> np.ndarray:
    fraction = (cutoff - first_height) / (second_height - first_height)
    return first + fraction[:, None] * (second - first)


def _clipped_bottom_projection(
    triangle_vertices: np.ndarray,
    triangle_heights: np.ndarray,
    cutoff: float,
    horizontal: list[int],
) -> tuple[float, np.ndarray]:
    """Return projected area and support points of triangles below a height.

    Coordinates and area remain in input units. Exact linear clipping makes
    the proxy insensitive to subdivisions of a planar triangle.
    """
    below = triangle_heights <= cutoff
    counts = below.sum(axis=1)
    projected = triangle_vertices[:, :, horizontal]
    pieces: list[np.ndarray] = []
    total_area = 0.0

    full = counts == 3
    if np.any(full):
        tri = projected[full]
        total_area += float(
            (0.5 * np.abs(_cross_2d(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]))).sum()
        )
        pieces.append(tri.reshape((-1, 2)))

    single = counts == 1
    if np.any(single):
        tri = projected[single]
        heights = triangle_heights[single]
        low_index = np.argmax(below[single], axis=1)
        rows = np.arange(len(tri))
        next_index = (low_index + 1) % 3
        prev_index = (low_index + 2) % 3
        low = tri[rows, low_index]
        next_point = _edge_at_height(
            low, tri[rows, next_index], heights[rows, low_index],
            heights[rows, next_index], cutoff,
        )
        prev_point = _edge_at_height(
            low, tri[rows, prev_index], heights[rows, low_index],
            heights[rows, prev_index], cutoff,
        )
        total_area += float((0.5 * np.abs(_cross_2d(next_point - low, prev_point - low))).sum())
        pieces.extend((low, next_point, prev_point))

    double = counts == 2
    if np.any(double):
        tri = projected[double]
        heights = triangle_heights[double]
        high_index = np.argmin(below[double], axis=1)
        rows = np.arange(len(tri))
        first_index = (high_index + 1) % 3
        second_index = (high_index + 2) % 3
        first = tri[rows, first_index]
        second = tri[rows, second_index]
        high = tri[rows, high_index]
        second_cut = _edge_at_height(
            second, high, heights[rows, second_index], heights[rows, high_index], cutoff,
        )
        first_cut = _edge_at_height(
            first, high, heights[rows, first_index], heights[rows, high_index], cutoff,
        )
        twice_area = _cross_2d(second - first, second_cut - first)
        twice_area += _cross_2d(second_cut - first, first_cut - first)
        total_area += float((0.5 * np.abs(twice_area)).sum())
        pieces.extend((first, second, second_cut, first_cut))

    support_points = (
        np.concatenate(pieces, axis=0)
        if pieces else np.empty((0, 2), dtype=np.float64)
    )
    return total_area, support_points


def _polygon_area(points: np.ndarray) -> float:
    if len(points) < 3:
        return 0.0
    return float(abs(np.sum(_cross_2d(points, np.roll(points, -1, axis=0)))) * 0.5)


def _center_of_mass_or_proxy(
    mesh: o3d.geometry.TriangleMesh,
    vertices: np.ndarray,
    triangles: np.ndarray,
    areas: np.ndarray,
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
    scale: float,
) -> tuple[np.ndarray, str]:
    """Use a closed-surface volume centroid, otherwise a labelled surface proxy."""
    closed = False
    try:
        closed = bool(
            mesh.is_watertight()
            and mesh.is_edge_manifold(allow_boundary_edges=False)
            and mesh.is_orientable()
        )
    except RuntimeError:
        closed = False
    if closed:
        oriented = o3d.geometry.TriangleMesh(mesh)
        try:
            if oriented.orient_triangles():
                oriented_triangles = np.asarray(oriented.triangles, dtype=np.int64)
                reference = bounds_min + (bounds_max - bounds_min) * 0.5
                center = _volume_centroid(vertices, oriented_triangles, reference, scale)
                if center is not None:
                    return center, "closed_mesh_volume_centroid"
        except (RuntimeError, ValueError, FloatingPointError):
            pass
    surface_centroids = vertices[triangles].mean(axis=1)
    surface_center = np.average(surface_centroids, axis=0, weights=areas)
    return surface_center, "surface_area_centroid_proxy_not_volume_center_of_mass"


def _volume_centroid(
    vertices: np.ndarray,
    triangles: np.ndarray,
    reference: np.ndarray,
    scale: float,
) -> np.ndarray | None:
    points = vertices[triangles] - reference
    six_volumes = np.einsum(
        "ij,ij->i", points[:, 0], np.cross(points[:, 1], points[:, 2])
    )
    signed_volume6 = float(six_volumes.sum())
    volume_tolerance = np.finfo(np.float64).eps * scale**3 * 256.0
    if not np.isfinite(signed_volume6) or abs(signed_volume6) <= volume_tolerance:
        return None
    weighted_centroid = np.sum(
        six_volumes[:, None] * points.sum(axis=1), axis=0
    ) / (4.0 * signed_volume6)
    center = reference + weighted_centroid
    return center if np.isfinite(center).all() else None


def _symmetry_scores(
    triangle_vertices: np.ndarray,
    areas: np.ndarray,
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
    scale: float,
) -> dict[int, float]:
    center = (bounds_min + bounds_max) * 0.5
    samples = (_sample_surface_deterministically(triangle_vertices, areas) - center) / scale
    triangle_count = len(triangle_vertices)
    soup_vertices = ((triangle_vertices - center) / scale).reshape((-1, 3))
    soup_triangles = np.arange(triangle_count * 3, dtype=np.int32).reshape((-1, 3))
    surface = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(soup_vertices),
        o3d.utility.Vector3iVector(soup_triangles),
    )
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(surface))
    tolerance = 0.03
    scores: dict[int, float] = {}
    for vertical_axis in range(3):
        horizontal_axes = [axis for axis in range(3) if axis != vertical_axis]
        plane_scores = []
        for reflected_axis in horizontal_axes:
            mirrored = samples.copy()
            mirrored[:, reflected_axis] *= -1.0
            query = o3d.core.Tensor(mirrored.astype(np.float32), dtype=o3d.core.Dtype.Float32)
            closest = scene.compute_closest_points(query)["points"].numpy()
            distances = np.linalg.norm(query.numpy() - closest, axis=1)
            plane_scores.append(float(np.mean(np.exp(-distances / tolerance))))
        scores[vertical_axis] = float(np.mean(plane_scores))
    return scores


def _sample_surface_deterministically(
    triangle_vertices: np.ndarray, areas: np.ndarray
) -> np.ndarray:
    count = min(_SYMMETRY_SAMPLES, max(512, len(areas) * 2))
    cumulative = np.cumsum(areas)
    targets = (np.arange(count, dtype=np.float64) + 0.5) * (cumulative[-1] / count)
    triangle_ids = np.searchsorted(cumulative, targets, side="left")
    indices = np.arange(count, dtype=np.uint64)
    u = _radical_inverse(indices + 1, 2)
    v = _radical_inverse(indices + 1, 3)
    root_u = np.sqrt(u)
    weights = np.column_stack((1.0 - root_u, root_u * (1.0 - v), root_u * v))
    return np.einsum("ni,nij->nj", weights, triangle_vertices[triangle_ids])


def _radical_inverse(indices: np.ndarray, base: int) -> np.ndarray:
    values = indices.copy()
    result = np.zeros(len(indices), dtype=np.float64)
    factor = 1.0 / base
    while np.any(values):
        result += (values % base) * factor
        values //= base
        factor /= base
    return result


def _convex_hull_2d(points: np.ndarray, scale: float) -> np.ndarray:
    if len(points) < 3:
        return np.empty((0, 2), dtype=np.float64)
    points = np.unique(np.asarray(points, dtype=np.float64), axis=0)
    if len(points) < 3:
        return np.empty((0, 2), dtype=np.float64)
    turn_tolerance = np.finfo(np.float64).eps * scale * scale * 64.0

    def cross(origin: np.ndarray, first: np.ndarray, second: np.ndarray) -> float:
        a = first - origin
        b = second - origin
        return float(a[0] * b[1] - a[1] * b[0])

    ordered = [point for point in points]
    lower: list[np.ndarray] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= turn_tolerance:
            lower.pop()
        lower.append(point)
    upper: list[np.ndarray] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= turn_tolerance:
            upper.pop()
        upper.append(point)
    hull = np.asarray(lower[:-1] + upper[:-1], dtype=np.float64)
    return hull if len(hull) >= 3 else np.empty((0, 2), dtype=np.float64)


def _signed_polygon_margin(point: np.ndarray, polygon: np.ndarray, scale: float) -> float:
    if len(polygon) < 3:
        return -1.0
    edge_start = polygon
    edge = np.roll(polygon, -1, axis=0) - polygon
    lengths = np.linalg.norm(edge, axis=1)
    nonzero = lengths > np.finfo(np.float64).eps * scale
    if not np.any(nonzero):
        return -1.0
    relative = point - edge_start
    signed_distances = (
        edge[:, 0] * relative[:, 1] - edge[:, 1] * relative[:, 0]
    ) / np.maximum(lengths, np.finfo(np.float64).tiny)
    signed_distance = float(np.min(signed_distances[nonzero]))
    return float(np.clip(signed_distance / scale, -1.0, 1.0))


def _ambiguous_degenerate_result(reason: str) -> dict[str, object]:
    candidates = [
        {
            "axis": axis,
            "score": 0.0,
            "contact_area_ratio": 0.0,
            "center_of_mass_margin": -1.0,
            "symmetry_score": 0.0,
            "center_of_mass_source": "unavailable_degenerate_geometry",
            "support_score": 0.0,
            "stability_score": 0.0,
            "support_proxy_score": 0.0,
            "verticality_score": 0.0,
            "contact_area_input_units_squared": 0.0,
            "support_hull_area_input_units_squared": 0.0,
            "freestanding_assessment": "unavailable_degenerate_geometry",
        }
        for axis in _AXES
    ]
    return {
        "selected_axis": None,
        "confidence": 0.0,
        "confidence_definition": "几何退化，无法计算；非概率",
        "axis_family": None,
        "axis_family_confidence": 0.0,
        "sign_contact_contrast": 0.0,
        "ambiguous": True,
        "candidates": candidates,
        "reason": reason,
        "center_of_mass_source": "unavailable_degenerate_geometry",
        "score_weights": dict(_DISPLAY_WEIGHTS),
        "stability_score_weights": dict(_SUPPORT_WEIGHTS),
        "support_proxy_score_weights": dict(_SUPPORT_WEIGHTS),
        "contact_definition": "几何退化，未计算",
        "ambiguity_gap_threshold": _AMBIGUITY_GAP,
        "minimum_contact_area_ratio": _MIN_CONTACT_AREA_RATIO,
        "minimum_balance_margin": _MIN_BALANCE_MARGIN,
        "minimum_sign_contact_contrast": _MIN_SIGN_CONTACT_CONTRAST,
        "slender_aspect_ratio_threshold": _SLENDER_ASPECT_RATIO,
        "slender_minimum_contact_area_ratio": _SLENDER_MIN_CONTACT_AREA_RATIO,
        "slender_contact_exception_used": False,
        "symmetry_definition": "几何退化，未计算",
        "units": "输入坐标单位；未推断或转换单位",
    }
