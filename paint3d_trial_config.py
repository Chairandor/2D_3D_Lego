import numpy as np
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple


@dataclass
class RenderConfig:
    """Low-memory, object-agnostic Paint3D rendering configuration."""

    grid_size: int = 1024
    radius: float = 1.5
    look_at_height: float = 0.0
    base_theta: float = 90.0
    fov_para: float = np.pi / 3 * 0.9
    remove_mesh_part_names: List[str] = field(default_factory=list)
    remove_unsupported_buffers: List[str] = field(default_factory=list)
    n_views: int = 24
    views_before: List[Tuple[float, float]] = field(default_factory=list)
    views_after: List[Tuple[float, float]] = field(default_factory=list)
    alternate_views: bool = True
    calcu_uncolored_mode: str = "WarpGrid"
    projection_mode: str = "Pinhole"
    texture_interpolation_mode: str = "bilinear"
    texture_default_color: List[float] = field(
        default_factory=lambda: [0.8, 0.1, 0.8]
    )
    texturify_blend_alpha: float = 1.0
    render_angle_thres: float = 68.0
    # With alternating azimuth ordering, indices 0 and 23 are front/back.
    views_init: List[int] = field(default_factory=lambda: [0, 23])
    views_inpaint: List[Tuple[int, int]] = field(
        default_factory=lambda: [(5, 6), (20, 21)]
    )


@dataclass
class GuideConfig:
    shape_path: str = ""
    initial_texture: Path | None = None
    texture_resolution: List[int] = field(default_factory=lambda: [1024, 1024])
    append_direction: bool = True
    shape_scale: float = 0.6
    z_update_thr: float = 0.2
    strict_projection: bool = True
    force_run_xatlas: bool = False


@dataclass
class OptimConfig:
    seed: int = 20260928
    lr: float = 1e-2
    train_step: int = 200


@dataclass
class LogConfig:
    exp_path: str = ""
    full_eval_size: int = 36
    cache_path: str = r"E:\3D_TEST\runs\7ken-paint3d\uv_cache"


@dataclass
class TrainConfig:
    log: LogConfig = field(default_factory=LogConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    optim: OptimConfig = field(default_factory=OptimConfig)
    guide: GuideConfig = field(default_factory=GuideConfig)
