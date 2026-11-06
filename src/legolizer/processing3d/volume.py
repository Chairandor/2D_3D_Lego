from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class VoxelVolume:
    """三维占用网格；origin_mm 是索引 (0, 0, 0) 单元的最小角。"""

    occupancy: np.ndarray
    voxel_size_mm: tuple[float, float, float]
    origin_mm: tuple[float, float, float]
    semantic: str = "solid"
    source: str | None = None
    colors: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.occupancy.ndim != 3:
            raise ValueError("occupancy 必须是三维数组")
        if not np.any(self.occupancy):
            raise ValueError("occupancy 不能为空")
        if any(v <= 0 for v in self.voxel_size_mm):
            raise ValueError("voxel_size_mm 必须全部大于 0")
        if self.colors is not None:
            if self.colors.shape != self.occupancy.shape:
                raise ValueError("colors 必须与 occupancy 形状一致")
            if not np.issubdtype(self.colors.dtype, np.integer):
                raise ValueError("colors 必须是 LDraw 色号整数数组")

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "voxel_size_mm": self.voxel_size_mm,
            "origin_mm": self.origin_mm,
            "semantic": self.semantic,
            "source": self.source,
            "axis_order": "xyz",
        }
        payload: dict[str, np.ndarray] = {
            "occupancy": self.occupancy.astype(np.uint8),
            "metadata": np.array(json.dumps(metadata, ensure_ascii=False)),
        }
        if self.colors is not None:
            payload["colors"] = self.colors.astype(np.int16)
        np.savez_compressed(target, **payload)

    @classmethod
    def load(cls, path: str | Path) -> "VoxelVolume":
        with np.load(path, allow_pickle=False) as data:
            metadata = json.loads(str(data["metadata"]))
            occupancy = data["occupancy"].astype(bool)
            colors = data["colors"].astype(np.int16) if "colors" in data else None
        return cls(
            occupancy=occupancy,
            voxel_size_mm=tuple(float(v) for v in metadata["voxel_size_mm"]),
            origin_mm=tuple(float(v) for v in metadata["origin_mm"]),
            semantic=metadata["semantic"],
            source=metadata.get("source"),
            colors=colors,
        )
