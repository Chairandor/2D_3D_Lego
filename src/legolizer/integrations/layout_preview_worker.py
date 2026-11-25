"""Generate a layout through the installed legolization backend, without its publication stages."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import numpy as np

from legolization.catalog import default_catalog
from legolization.color import default_palette
from legolization.configuration import StabilityConfig
from legolization.graph import ConnectionGraph
from legolization.grid import EMPTY, IGNORE, VoxelGrid
from legolization.ldraw_out import write_model
from legolization.pipeline import PipelineConfig
from legolization.placement.merge import resolve_ignore_colours
from legolization.placement.registry import make_strategy


def main() -> None:
    input_name, output_name, seconds = sys.argv[1:]
    budget = float(seconds)
    codes = np.load(input_name, allow_pickle=False)
    unknown = np.setdiff1d(np.unique(codes), np.r_[default_palette().codes, EMPTY, IGNORE])
    if unknown.size:
        raise ValueError(f"Unknown LDraw colours: {unknown.tolist()}")
    config = PipelineConfig(strategy="fast", solver=StabilityConfig(support="table").effective_solver(),
                            time_budget_s=min(8., budget), refine=False, milp_bridge=False,
                            connectivity_fail_max=0)
    grid = VoxelGrid(np.repeat(codes, config.plates_per_voxel, axis=2))
    strategy = make_strategy("fast", catalog=default_catalog(), config=config)
    strategy.retry_max = 1
    start = time.monotonic()
    layout = strategy.place(grid, rng=np.random.default_rng(0), deadline=start + budget)
    resolve_ignore_colours(layout)
    path = Path(output_name)
    temporary = path.with_name("layout.tmp.ldr")
    write_model(layout, temporary, steps=False)
    temporary.replace(path)
    elapsed = time.monotonic() - start
    topology = ConnectionGraph.from_layout(layout).topology_metrics()
    print(json.dumps(dict(brick_count=len(layout), placement_seconds=round(elapsed, 2),
                          components=topology.component_count, floating=topology.floating_count)), flush=True)


if __name__ == "__main__":
    main()
