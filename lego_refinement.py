"""Optional, repository-local refinements for the SF3D-to-LEGO pipeline.

This module intentionally does not patch SF3D or legolization. The operations
are conservative: uncertain surfaces and colours retain their original values.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import cg
from trimesh.visual.color import ColorVisuals


def recover_dark_image_details(mesh, sf3d_input: Path) -> tuple[np.ndarray, dict]:
    """Restore small, interior, high-contrast dark markings in the input view.

    SF3D's exported Y-up frame has its input camera at -Z, screen-right at -X.
    Only vertices facing that camera are changed. Dark boundary/shadow regions
    are excluded using foreground distance and local contrast tests.
    """
    from PIL import Image

    protected = np.zeros(len(mesh.vertices), dtype=bool)
    report = {"dark_regions": 0, "protected_vertices": 0}
    if not sf3d_input.is_file() or len(mesh.vertices) == 0:
        return protected, report
    rgba = np.asarray(Image.open(sf3d_input).convert("RGBA"))
    height, width = rgba.shape[:2]
    foreground = rgba[:, :, 3] >= 128
    rgb = rgba[:, :, :3]
    dark = foreground & (rgb.max(axis=2) < 96)
    labels, count = ndimage.label(dark)
    distance_to_edge = ndimage.distance_transform_edt(foreground)
    vertices = np.asarray(mesh.vertices)
    normals = np.asarray(mesh.vertex_normals)
    focal = 0.5 * height / np.tan(np.deg2rad(20.0))
    depth = 1.6 + vertices[:, 2]
    valid = (depth > 0.1) & (normals[:, 2] < -0.25)
    px = np.rint(width / 2 - focal * vertices[:, 0] / depth).astype(int)
    py = np.rint(height / 2 - focal * vertices[:, 1] / depth).astype(int)
    valid &= (px >= 0) & (px < width) & (py >= 0) & (py < height)
    colours = np.asarray(mesh.visual.vertex_colors).copy()
    minimum_area = max(20, round(0.00003 * int(foreground.sum())))
    for region in range(1, count + 1):
        region_mask = labels == region
        area = int(region_mask.sum())
        if area < minimum_area or area > 0.04 * foreground.sum():
            continue
        if float(np.median(distance_to_edge[region_mask])) < 12.0:
            continue
        outer = ndimage.binary_dilation(region_mask, iterations=18)
        inner = ndimage.binary_dilation(region_mask, iterations=3)
        ring = outer & ~inner & foreground
        if int(ring.sum()) < minimum_area:
            continue
        region_value = float(np.median(rgb[region_mask].max(axis=1)))
        ring_value = float(np.median(rgb[ring].max(axis=1)))
        if ring_value - region_value < 95.0:
            continue
        hits = np.zeros(len(vertices), dtype=bool)
        hits[valid] = region_mask[py[valid], px[valid]]
        if not hits.any():
            continue
        colours[hits, :3] = np.median(rgb[region_mask], axis=0).astype(np.uint8)
        protected |= hits
        report["dark_regions"] += 1
    if protected.any():
        mesh.visual = ColorVisuals(vertex_colors=colours)
    report["protected_vertices"] = int(protected.sum())
    return protected, report


def remove_tiny_colour_islands(mesh, protected: np.ndarray | None = None) -> dict:
    """Remove tiny disconnected colour flecks without erasing marked details."""
    count = len(mesh.vertices)
    if count == 0:
        return {"recoloured_vertices": 0, "recoloured_islands": 0}
    protected = np.zeros(count, dtype=bool) if protected is None else protected
    colours = np.asarray(mesh.visual.vertex_colors).copy()
    rgb = colours[:, :3]
    edges = np.asarray(mesh.edges_unique, dtype=np.int64)
    graph = sparse.csr_matrix(
        (np.ones(2 * len(edges), dtype=np.uint8),
         (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(count, count),
    )
    replacement = rgb.copy()
    max_island = max(3, min(8, round(count * 0.0004)))
    changed_islands = 0
    for colour in np.unique(rgb, axis=0):
        members = np.flatnonzero(np.all(rgb == colour, axis=1))
        _, labels = connected_components(graph[members][:, members], directed=False)
        for component in range(int(labels.max()) + 1):
            island = members[labels == component]
            if len(island) > max_island or protected[island].any():
                continue
            neighbours = graph[island].indices
            neighbours = neighbours[~np.all(rgb[neighbours] == colour, axis=1)]
            if len(neighbours) < 2:
                continue
            candidates, frequency = np.unique(rgb[neighbours], axis=0, return_counts=True)
            winner = candidates[int(np.argmax(frequency))]
            if int(frequency.max()) < 2:
                continue
            replacement[island] = winner
            changed_islands += 1
    changed = int(np.count_nonzero(np.any(replacement != rgb, axis=1)))
    if changed:
        colours[:, :3] = replacement
        mesh.visual = ColorVisuals(vertex_colors=colours)
    return {"recoloured_vertices": changed, "recoloured_islands": changed_islands}


def planarize_broad_surfaces(mesh) -> dict:
    """Flatten only large, nearly axis-aligned patches after upright alignment.

    A patch must have aligned face normals, be connected, and fit a plane with
    small residual. Boundary vertices stay fixed, preserving silhouettes and
    small appendages. This is a LEGO-specific regularizer, not a general mesh
    smoothing filter.
    """
    faces = np.asarray(mesh.faces)
    if len(faces) == 0:
        return {"patches": 0, "moved_vertices": 0, "maximum_move": 0.0}
    normals = np.asarray(mesh.face_normals)
    areas = np.asarray(mesh.area_faces)
    vertices = np.asarray(mesh.vertices)
    scale = float(np.max(mesh.extents))
    axis = np.abs(normals).argmax(axis=1)
    alignment = np.abs(normals[np.arange(len(faces)), axis])
    sign = normals[np.arange(len(faces)), axis] < 0
    category = np.where(alignment >= np.cos(np.deg2rad(25)), axis * 2 + sign, -1)
    adjacency = np.asarray(mesh.face_adjacency)
    linked = adjacency[category[adjacency[:, 0]] == category[adjacency[:, 1]]]
    linked = linked[category[linked[:, 0]] >= 0]
    graph = sparse.csr_matrix(
        (np.ones(2 * len(linked), dtype=np.uint8),
         (np.r_[linked[:, 0], linked[:, 1]], np.r_[linked[:, 1], linked[:, 0]])),
        shape=(len(faces), len(faces)),
    )
    _, components = connected_components(graph, directed=False)
    total_incident = np.bincount(faces.ravel(), minlength=len(vertices))
    updated = vertices.copy()
    moved = np.zeros((len(vertices), 3), dtype=bool)
    patches = 0
    total_area = float(areas.sum())
    for component in np.unique(components[category >= 0]):
        face_ids = np.flatnonzero((components == component) & (category >= 0))
        if len(face_ids) < 30 or float(areas[face_ids].sum()) < 0.025 * total_area:
            continue
        plane_axis = int(axis[face_ids[0]])
        centroids = np.asarray(mesh.triangles_center)[face_ids, plane_axis]
        plane = float(np.median(centroids))
        if float(np.percentile(np.abs(centroids - plane), 80)) > 0.025 * scale:
            continue
        incident = np.bincount(faces[face_ids].ravel(), minlength=len(vertices))
        # Include a plane's boundary vertices. Otherwise its interior becomes
        # flat but the pinned perimeter turns into a visibly jagged ridge.
        ownership = incident / np.maximum(total_incident, 1)
        candidates = (incident > 0) & (ownership >= 0.33) & ~moved[:, plane_axis]
        displacement = plane - vertices[:, plane_axis]
        candidates &= np.abs(displacement) <= 0.035 * scale
        if int(candidates.sum()) < 10:
            continue
        step = np.clip(displacement[candidates], -0.018 * scale, 0.018 * scale)
        updated[candidates, plane_axis] += step
        moved[candidates, plane_axis] = True
        patches += 1
    maximum_move = float(np.max(np.linalg.norm(updated - vertices, axis=1)))
    if moved.any():
        mesh.vertices = updated
    return {
        "patches": patches,
        "moved_vertices": int(moved.any(axis=1).sum()),
        "maximum_move": maximum_move,
    }


def smooth_non_feature_surfaces(mesh, iterations: int = 4) -> dict:
    """Dampen small geometric ripples while preserving likely hard edges.

    Unlike broad plane snapping, each update is local and normal-direction
    only. Vertices on large normal discontinuities are pinned. The total move
    is capped well below a 12-stud voxel for this mesh, so this cannot repair
    grossly incorrect SF3D geometry but should not create new ledges either.
    """
    vertices = np.asarray(mesh.vertices).copy()
    count = len(vertices)
    if count == 0:
        return {"moved_vertices": 0, "maximum_move": 0.0}
    scale = float(np.max(mesh.extents))
    edges = np.asarray(mesh.edges_unique)
    graph = sparse.csr_matrix(
        (np.ones(2 * len(edges), dtype=np.float64),
         (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(count, count),
    )
    degree = np.asarray(graph.sum(axis=1)).ravel()
    original_normals = np.asarray(mesh.vertex_normals)
    dot = np.sum(original_normals[edges[:, 0]] * original_normals[edges[:, 1]], axis=1)
    sharp = edges[dot < np.cos(np.deg2rad(25))]
    pinned = np.zeros(count, dtype=bool)
    pinned[sharp.ravel()] = True
    original = vertices.copy()
    for _ in range(iterations):
        mean = (graph @ vertices) / np.maximum(degree[:, None], 1.0)
        normal = np.asarray(mesh.vertex_normals)
        projection = np.sum((mean - vertices) * normal, axis=1)
        step = np.clip(0.45 * projection, -0.004 * scale, 0.004 * scale)
        step[pinned] = 0.0
        vertices += step[:, None] * normal
        mesh.vertices = vertices
    movement = np.linalg.norm(vertices - original, axis=1)
    return {
        "moved_vertices": int(np.count_nonzero(movement > 1e-6)),
        "maximum_move": float(movement.max()),
        "pinned_vertices": int(pinned.sum()),
    }


def guided_normal_refine(
    mesh,
    *,
    normal_iterations: int = 10,
    vertex_iterations: int = 8,
) -> dict:
    """Denoise geometry using a bilateral face-normal field and vertex updates.

    The weights depend on *local* differences between adjacent faces, not
    global XYZ axes, object labels, source colours, or a target object class.
    Neighbouring faces across a strong crease contribute very little to each
    other's normals. Vertex positions then solve local incident-plane
    constraints with a small displacement budget and a tether to the input.
    """
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return {"moved_vertices": 0, "maximum_move": 0.0}
    original = np.asarray(mesh.vertices, dtype=np.float64).copy()
    current = original.copy()
    scale = float(np.max(mesh.extents))
    areas = np.maximum(np.asarray(mesh.area_faces, dtype=np.float64), 1e-12)
    original_normals = np.asarray(mesh.face_normals, dtype=np.float64).copy()
    adjacent = np.asarray(mesh.face_adjacency, dtype=np.int64)
    left, right = adjacent[:, 0], adjacent[:, 1]
    similarity = np.clip(np.sum(original_normals[left] * original_normals[right], axis=1), -1.0, 1.0)
    angle = np.arccos(similarity)
    # Bilateral angular kernel: a right-angle crease receives essentially no
    # cross-edge smoothing, while nearby rippled facets support one another.
    pair_weight = np.exp(-0.5 * (angle / np.deg2rad(25.0)) ** 2)
    normals = original_normals.copy()
    for _ in range(normal_iterations):
        accumulation = normals * areas[:, None]
        denominator = areas.copy()
        np.add.at(accumulation, left, pair_weight[:, None] * areas[right, None] * normals[right])
        np.add.at(accumulation, right, pair_weight[:, None] * areas[left, None] * normals[left])
        np.add.at(denominator, left, pair_weight * areas[right])
        np.add.at(denominator, right, pair_weight * areas[left])
        normals = accumulation / denominator[:, None]
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-12)

    max_total_move = 0.02 * scale
    for _ in range(vertex_iterations):
        centres = current[faces].mean(axis=1)
        displacement = np.zeros_like(current)
        incident_area = np.zeros(len(current), dtype=np.float64)
        for corner in range(3):
            indices = faces[:, corner]
            offset = centres - current[indices]
            distance = np.sum(offset * normals, axis=1)
            np.add.at(displacement, indices, normals * (distance * areas)[:, None])
            np.add.at(incident_area, indices, areas)
        update = 0.7 * displacement / np.maximum(incident_area[:, None], 1e-12)
        update -= 0.12 * (current - original)
        candidate = current + update
        drift = candidate - original
        magnitude = np.linalg.norm(drift, axis=1)
        factor = np.minimum(1.0, max_total_move / np.maximum(magnitude, 1e-12))
        current = original + drift * factor[:, None]
    mesh.vertices = current
    movement = np.linalg.norm(current - original, axis=1)
    return {
        "moved_vertices": int(np.count_nonzero(movement > 1e-6)),
        "maximum_move": float(movement.max()),
        "mean_move": float(movement.mean()),
    }


def multiscale_surface_fairing(mesh, *, strength: float = 8.0) -> dict:
    """Suppress high-frequency vertex noise while retaining broad shape.

    A bi-Laplacian data fit damps short-wavelength ripples more than broad
    curvature. Its graph does not cross likely creases, which are estimated
    from neighbourhood-averaged normals rather than global axes or colours.
    This is experimental and is not yet part of the default pipeline.
    """
    original = np.asarray(mesh.vertices, dtype=np.float64).copy()
    count = len(original)
    if count == 0:
        return {"moved_vertices": 0, "maximum_move": 0.0}
    edges = np.asarray(mesh.edges_unique, dtype=np.int64)
    if len(edges) == 0:
        return {"moved_vertices": 0, "maximum_move": 0.0}
    rows = np.r_[edges[:, 0], edges[:, 1]]
    columns = np.r_[edges[:, 1], edges[:, 0]]
    unit_graph = sparse.csr_matrix(
        (np.ones(len(rows)), (rows, columns)), shape=(count, count)
    )
    degree = np.asarray(unit_graph.sum(axis=1)).ravel()
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    for _ in range(2):
        normals = normals + (unit_graph @ normals) / np.maximum(degree[:, None], 1.0)
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-12)
    dot = np.clip(np.sum(normals[edges[:, 0]] * normals[edges[:, 1]], axis=1), -1.0, 1.0)
    angle = np.arccos(dot)
    weights = np.where(
        angle < np.deg2rad(32.0),
        np.exp(-0.5 * (angle / np.deg2rad(23.0)) ** 2),
        0.0,
    )
    protected = np.zeros(count, dtype=bool)
    protected[edges[angle >= np.deg2rad(32.0)].ravel()] = True
    graph = sparse.csr_matrix(
        (np.r_[weights, weights], (rows, columns)), shape=(count, count)
    )
    degree = np.asarray(graph.sum(axis=1)).ravel()
    laplacian = sparse.eye(count, format="csr") - sparse.diags(
        1.0 / np.maximum(degree, 1e-12)
    ) @ graph
    system = sparse.eye(count, format="csr") + strength * (laplacian.T @ laplacian)
    smoothed = np.column_stack([
        cg(system, original[:, axis], rtol=1e-8, maxiter=500)[0]
        for axis in range(3)
    ])
    drift = smoothed - original
    input_normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    drift = np.sum(drift * input_normals, axis=1)[:, None] * input_normals
    drift[protected] = 0.0
    scale = float(np.max(mesh.extents))
    magnitude = np.linalg.norm(drift, axis=1)
    cap = 0.025 * scale
    smoothed = original + drift * np.minimum(1.0, cap / np.maximum(magnitude, 1e-12))[:, None]
    mesh.vertices = smoothed
    movement = np.linalg.norm(smoothed - original, axis=1)
    return {
        "moved_vertices": int(np.count_nonzero(movement > 1e-6)),
        "maximum_move": float(movement.max()),
        "mean_move": float(movement.mean()),
    }


def _connected_face_patch(seed: int, eligible: np.ndarray, neighbours: list[list[int]]) -> np.ndarray:
    """Find the mesh-connected part of one candidate plane without axis assumptions."""
    if not eligible[seed]:
        return np.empty(0, dtype=np.int64)
    found = np.zeros(len(eligible), dtype=bool)
    found[seed] = True
    queue = [seed]
    for face in queue:
        for other in neighbours[face]:
            if eligible[other] and not found[other]:
                found[other] = True
                queue.append(other)
    return np.asarray(queue, dtype=np.int64)


def adaptive_plane_refine(mesh) -> dict:
    """Find confident broad planes and solve their shared vertex constraints.

    Curved/uncertain surfaces are not assigned a plane. The solver works in
    the mesh's own coordinate frame, so a tilted box and an upright one are
    treated identically. Constraints are diffused across adjoining vertices
    rather than independently snapping patch interiors to avoid raised rims.
    """
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) == 0:
        return {"planes": 0, "moved_vertices": 0, "maximum_move": 0.0}
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    centres = np.asarray(mesh.triangles_center, dtype=np.float64)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    scale = float(np.max(mesh.extents))
    total_area = float(areas.sum())
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    neighbours: list[list[int]] = [[] for _ in range(len(faces))]
    for left, right in adjacency:
        neighbours[left].append(int(right))
        neighbours[right].append(int(left))

    claimed = np.zeros(len(faces), dtype=bool)
    planes: list[tuple[np.ndarray, float, np.ndarray]] = []
    # Large triangles alone are not representative: a tessellated cap can
    # have much smaller triangles than the object's curved side. Sample each
    # occupied normal-direction bin as well as the largest triangles.
    area_order = np.argsort(areas)[::-1]
    direction_bins = np.rint(normals[area_order] / 0.35).astype(np.int8)
    _, first_in_bin = np.unique(direction_bins, axis=0, return_index=True)
    seeds = np.concatenate((
        area_order[np.sort(first_in_bin)],
        area_order[: min(250, len(faces))],
    ))
    for seed in seeds:
        if claimed[seed]:
            continue
        direction = normals[seed].copy()
        offset = float(centres[seed] @ direction)
        patch = np.empty(0, dtype=np.int64)
        for _ in range(3):
            aligned = normals @ direction > np.cos(np.deg2rad(28.0))
            coplanar = np.abs(centres @ direction - offset) < 0.035 * scale
            patch = _connected_face_patch(
                int(seed), aligned & coplanar & ~claimed, neighbours
            )
            if len(patch) < 30:
                break
            candidate = (normals[patch] * areas[patch, None]).sum(axis=0)
            direction = candidate / max(float(np.linalg.norm(candidate)), 1e-12)
            offset = float(np.median(centres[patch] @ direction))
        if len(patch) < 30 or float(areas[patch].sum()) < 0.035 * total_area:
            continue
        deviation = np.abs(centres[patch] @ direction - offset)
        normal_spread = np.arccos(np.clip(normals[patch] @ direction, -1.0, 1.0))
        if (
            float(np.percentile(deviation, 85)) > 0.015 * scale
            or float(np.percentile(normal_spread, 80)) > np.deg2rad(20.0)
        ):
            continue
        # A thin ribbon on a cylinder is locally close to a plane, but its
        # support is narrow in one tangent direction. Do not flatten such a
        # curved wall merely because it contributes substantial area.
        patch_centres = centres[patch]
        patch_weights = areas[patch] / max(float(areas[patch].sum()), 1e-12)
        centred = patch_centres - np.average(patch_centres, axis=0, weights=patch_weights)
        tangent = centred - (centred @ direction)[:, None] * direction
        covariance = (tangent * patch_weights[:, None]).T @ tangent
        tangent_spread, tangent_axes = np.linalg.eigh(covariance)
        if 2.0 * np.sqrt(max(float(tangent_spread[-2]), 0.0)) < 0.12 * scale:
            continue
        # Reject coherent second-order bending. A cylinder strip can be
        # spatially wide enough to pass the support test while still being
        # a genuine curved surface rather than a noisy plane.
        uv = tangent @ tangent_axes[:, -2:]
        design = np.column_stack((
            uv[:, 0] ** 2,
            uv[:, 0] * uv[:, 1],
            uv[:, 1] ** 2,
            uv[:, 0],
            uv[:, 1],
            np.ones(len(patch)),
        ))
        residual = patch_centres @ direction - offset
        weighted_design = design * np.sqrt(patch_weights[:, None])
        weighted_residual = residual * np.sqrt(patch_weights)
        quadratic = np.linalg.lstsq(weighted_design, weighted_residual, rcond=None)[0]
        linear = np.linalg.lstsq(weighted_design[:, 3:], weighted_residual, rcond=None)[0]
        bend = design @ quadratic - design[:, 3:] @ linear
        bend_rms = np.sqrt(np.average(bend ** 2, weights=patch_weights))
        if bend_rms > 0.003 * scale:
            continue
        planes.append((direction, offset, patch))
        claimed[patch] = True
        if len(planes) >= 16:
            break
    if not planes:
        return {"planes": 0, "moved_vertices": 0, "maximum_move": 0.0}

    count = len(vertices)
    incidence = np.bincount(faces.ravel(), minlength=count)
    matrix = np.zeros((count, 3, 3), dtype=np.float64)
    vector = np.zeros((count, 3), dtype=np.float64)
    confidence = np.zeros(count, dtype=np.float64)
    for direction, offset, patch in planes:
        included = np.bincount(faces[patch].ravel(), minlength=count)
        ids = np.flatnonzero(included)
        weight = included[ids] / np.maximum(incidence[ids], 1)
        error = offset - vertices[ids] @ direction
        matrix[ids] += weight[:, None, None] * np.outer(direction, direction)
        vector[ids] += (weight * error)[:, None] * direction
        confidence[ids] += weight
    matrix += 0.08 * np.eye(3)[None, :, :]
    desired = np.linalg.solve(matrix, vector[..., None])[..., 0]

    edges = np.asarray(mesh.edges_unique)
    vertex_normals = np.asarray(mesh.vertex_normals)
    similarity = np.clip(
        np.sum(vertex_normals[edges[:, 0]] * vertex_normals[edges[:, 1]], axis=1),
        -1.0,
        1.0,
    )
    edge_angle = np.arccos(similarity)
    edge_weight = np.exp(-0.5 * (edge_angle / np.deg2rad(30.0)) ** 2)
    graph = sparse.csr_matrix(
        (np.r_[edge_weight, edge_weight],
         (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(count, count),
    )
    degree = np.asarray(graph.sum(axis=1)).ravel()
    displacement = desired.copy()
    for _ in range(12):
        mean = (graph @ displacement) / np.maximum(degree[:, None], 1e-12)
        displacement = (
            confidence[:, None] * desired + 0.35 * mean
        ) / (confidence[:, None] + 0.43)
    magnitude = np.linalg.norm(displacement, axis=1)
    factor = np.minimum(1.0, (0.025 * scale) / np.maximum(magnitude, 1e-12))
    displacement *= factor[:, None]
    mesh.vertices = vertices + displacement
    return {
        "planes": len(planes),
        "moved_vertices": int(np.count_nonzero(np.linalg.norm(displacement, axis=1) > 1e-6)),
        "maximum_move": float(np.max(np.linalg.norm(displacement, axis=1))),
    }


def flatten_shallow_markings(mesh, protected: np.ndarray) -> dict:
    """Treat selected 2D markings as colour when they sit on a flat surface.

    This does not infer that every dark area is paint: only the conservative
    high-contrast, interior regions recovered from the source image reach this
    function, and the surrounding 3D surface must independently fit a plane.
    """
    report = {"flattened_vertices": 0, "maximum_move": 0.0}
    if not protected.any():
        return report
    vertices = np.asarray(mesh.vertices)
    normals = np.asarray(mesh.vertex_normals)
    scale = float(np.max(mesh.extents))
    adjusted = vertices.copy()
    marked_indices = np.flatnonzero(protected)
    edges = np.asarray(mesh.edges_unique)
    graph = sparse.csr_matrix(
        (np.ones(2 * len(edges), dtype=np.uint8),
         (np.r_[edges[:, 0], edges[:, 1]], np.r_[edges[:, 1], edges[:, 0]])),
        shape=(len(vertices), len(vertices)),
    )
    _, components = connected_components(
        graph[marked_indices][:, marked_indices], directed=False
    )
    for component in np.unique(components):
        target_indices = marked_indices[components == component]
        if len(target_indices) < 3:
            continue
        marked = vertices[target_indices]
        centroid = np.median(marked, axis=0)
        marked_normal = np.median(normals[target_indices], axis=0)
        marked_normal /= max(float(np.linalg.norm(marked_normal)), 1e-9)
        distances = np.linalg.norm(vertices - centroid, axis=1)
        ring = (
            (~protected)
            & (distances < 0.13 * scale)
            & (distances > 0.025 * scale)
            & (normals @ marked_normal > np.cos(np.deg2rad(30)))
        )
        if int(ring.sum()) < 40:
            continue
        # Keep the closest support, excluding neighbouring appendages.
        ring_indices = np.flatnonzero(ring)
        ring_indices = ring_indices[np.argsort(distances[ring_indices])[:800]]
        support = vertices[ring_indices]
        support_center = np.median(support, axis=0)
        _, _, vectors = np.linalg.svd(support - support_center, full_matrices=False)
        plane_normal = vectors[-1]
        residual = (support - support_center) @ plane_normal
        if float(np.percentile(np.abs(residual), 80)) > 0.012 * scale:
            continue
        offsets = (marked - support_center) @ plane_normal
        eligible = np.abs(offsets) <= 0.025 * scale
        if not eligible.any():
            continue
        target = target_indices[eligible]
        adjusted[target] -= offsets[eligible, None] * plane_normal
        report["flattened_vertices"] += int(len(target))
        report["maximum_move"] = max(
            report["maximum_move"], float(np.max(np.abs(offsets[eligible])))
        )
    if report["flattened_vertices"]:
        mesh.vertices = adjusted
    return report
