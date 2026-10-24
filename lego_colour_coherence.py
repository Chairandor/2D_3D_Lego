"""Conservative, geometry-aware cleanup of palette boundary flicker.

This operates on vertex colours after LEGO palette mapping, but uses the
pre-mapping colours to decide whether two mapped shades were really one
near-uniform material. It deliberately leaves broad gradients alone.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from trimesh.visual.color import ColorVisuals


def stabilize_palette_regions(
    mesh,
    source_rgb: np.ndarray,
    *,
    max_edge_distance: float = 0.05,
    max_group_median_distance: float = 0.02,
    max_region_spread: float = 0.04,
    min_region_vertices: int = 64,
) -> dict[str, object]:
    """Merge a two-shade region only when its original colour is nearly flat.

    Distances use Oklab (0..1 scale). Candidate regions are connected on the
    mesh, not in a flattened image; links across a sharp source-colour edge
    are excluded. A whole-region spread check prevents a gradual colour ramp
    from being merged merely because each adjacent pair looks similar.
    """
    from legolization.color import _srgb_to_oklab

    mapped_rgba = np.asarray(mesh.visual.vertex_colors)
    source_rgb = np.asarray(source_rgb)
    count = len(mesh.vertices)
    if mapped_rgba.shape[0] != count or source_rgb.shape != (count, 3):
        raise ValueError("source and mapped colours must match mesh vertices")
    if not count or not len(mesh.faces):
        return {"candidate_regions": 0, "merged_regions": 0, "recoloured_vertices": 0}

    mapped = mapped_rgba[:, :3]
    colours, labels = np.unique(mapped, axis=0, return_inverse=True)
    if len(colours) < 2:
        return {"candidate_regions": 0, "merged_regions": 0, "recoloured_vertices": 0}

    lab = _srgb_to_oklab(source_rgb)
    edges = np.asarray(mesh.edges_unique, dtype=np.int64)
    boundary = labels[edges[:, 0]] != labels[edges[:, 1]]
    if not boundary.any():
        return {"candidate_regions": 0, "merged_regions": 0, "recoloured_vertices": 0}
    pairs = np.unique(np.sort(labels[edges[boundary]], axis=1), axis=0)
    close_edge = np.linalg.norm(lab[edges[:, 0]] - lab[edges[:, 1]], axis=1) <= max_edge_distance
    proposals: list[tuple[int, float, np.ndarray, int]] = []
    candidate_regions = 0

    for first, second in pairs:
        active = (labels == first) | (labels == second)
        vertices = np.flatnonzero(active)
        local_index = np.full(count, -1, dtype=np.int32)
        local_index[vertices] = np.arange(len(vertices), dtype=np.int32)
        links = edges[active[edges[:, 0]] & active[edges[:, 1]] & close_edge]
        graph = sparse.csr_matrix(
            (np.ones(2 * len(links), dtype=np.uint8),
             (np.r_[local_index[links[:, 0]], local_index[links[:, 1]]],
              np.r_[local_index[links[:, 1]], local_index[links[:, 0]]])),
            shape=(len(vertices), len(vertices)),
        )
        _, components = connected_components(graph, directed=False)
        sizes = np.bincount(components)
        for component in np.flatnonzero(sizes >= min_region_vertices):
            members = vertices[components == component]
            first_members = members[labels[members] == first]
            second_members = members[labels[members] == second]
            if min(len(first_members), len(second_members)) < max(8, int(0.02 * len(members))):
                continue
            candidate_regions += 1
            median_gap = float(np.linalg.norm(
                np.median(lab[first_members], axis=0)
                - np.median(lab[second_members], axis=0)
            ))
            spread = float(np.linalg.norm(
                np.percentile(lab[members], 90, axis=0)
                - np.percentile(lab[members], 10, axis=0)
            ))
            if median_gap > max_group_median_distance or spread > max_region_spread:
                continue
            winner = int(first if len(first_members) >= len(second_members) else second)
            proposals.append((len(members), spread, members, winner))

    result = mapped_rgba.copy()
    claimed = np.zeros(count, dtype=bool)
    merged_regions = 0
    for _, _, members, winner in sorted(proposals, key=lambda item: (-item[0], item[1])):
        if claimed[members].any():
            continue  # Do not let overlapping two-shade proposals fight.
        result[members, :3] = colours[winner]
        claimed[members] = True
        merged_regions += 1
    recoloured = int(np.count_nonzero(np.any(result[:, :3] != mapped, axis=1)))
    if recoloured:
        mesh.visual = ColorVisuals(vertex_colors=result)
    return {
        "candidate_regions": candidate_regions,
        "merged_regions": merged_regions,
        "recoloured_vertices": recoloured,
    }
