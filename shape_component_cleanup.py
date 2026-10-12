"""Conservative, object-agnostic cleanup for isolated shape-model fragments."""

from __future__ import annotations

import numpy as np
import trimesh


def remove_negligible_components(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, dict]:
    """Remove fragments only when one component overwhelmingly dominates.

    This intentionally refuses to discard components when substantial
    disconnected parts could be real pieces of the object. The raw mesh must
    always be retained alongside any cleaned trial output.
    """
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh.copy(), {"components": len(parts), "removed": 0, "status": "unchanged"}
    counts = np.array([len(part.faces) for part in parts], dtype=np.int64)
    total = int(counts.sum())
    main_index = int(np.argmax(counts))
    main_fraction = float(counts[main_index] / max(total, 1))
    secondary_fraction = float(np.max(np.delete(counts, main_index)) / max(total, 1))
    if main_fraction < 0.95 or secondary_fraction > 0.005:
        return mesh.copy(), {
            "components": len(parts),
            "removed": 0,
            "status": "review_needed",
            "main_fraction": main_fraction,
            "largest_secondary_fraction": secondary_fraction,
        }
    cleaned = parts[main_index].copy()
    return cleaned, {
        "components": len(parts),
        "removed": len(parts) - 1,
        "status": "removed_negligible_fragments",
        "main_fraction": main_fraction,
        "largest_secondary_fraction": secondary_fraction,
        "raw_faces": len(mesh.faces),
        "clean_faces": len(cleaned.faces),
        "clean_watertight": bool(cleaned.is_watertight),
    }
