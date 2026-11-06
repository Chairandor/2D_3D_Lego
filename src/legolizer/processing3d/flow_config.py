"""三条文件流水线的纯标准库参数契约。"""

import math


FLOWS = {
    "stl": {"label": "STL → LEGO", "demo": "bunny.stl", "unit": 1000., "axis": "y"},
    "ply_plain": {"label": "无色 PLY → Mesh → LEGO", "demo": "rhino.ply", "unit": 1., "axis": "y"},
    "ply_color": {"label": "有色 PLY → Mesh / 贴图 → LEGO", "demo": "tiko_10000_points.ply", "unit": 1., "axis": "y"},
}



def validate_options(flow: str, options: dict) -> dict:
    """校验三条路径的参数；长度输入是源坐标到毫米的显式倍率。"""
    if flow not in FLOWS:
        raise ValueError("请选择 STL、无色 PLY 或有色 PLY 路径")
    result = dict(source_unit_mm=FLOWS[flow]["unit"], source_up_axis=FLOWS[flow]["axis"],
                  target_width_studs=12, poisson_depth=7, density_quantile=.005,
                  quality="direct", duration_seconds=60., color=4, instruction_mode="layer")
    for key in result:
        if key in options:
            result[key] = options[key]
    for key, low, high, integer in (("source_unit_mm", 1e-9, 1e9, False),
                                  ("target_width_studs", 4, 64, True),
                                  ("poisson_depth", 5, 10, True),
                                  ("density_quantile", 0, .2, False),
                                  ("duration_seconds", 5, 3600, False),
                                  ("color", 0, 10000, True)):
        value = result[key]
        if isinstance(value, bool):
            raise ValueError(f"{key} 必须为有效数字")
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"{key} 必须为有效数字") from None
        if not math.isfinite(number) or not low <= number <= high or (integer and not number.is_integer()):
            raise ValueError(f"{key} 超出范围 {low}–{high}")
        result[key] = int(number) if integer else number
    if result["source_up_axis"] not in {"x", "-x", "y", "-y", "z", "-z", "auto"}:
        raise ValueError("请选择有效的源上轴")
    if flow != "stl" and result["source_up_axis"] == "auto":
        raise ValueError("点云上轴请手动指定，暂不支持点云 auto")
    if result["quality"] not in {"fast", "balanced", "direct"}:
        raise ValueError("求解质量必须是 fast、balanced 或 direct")
    if result["instruction_mode"] not in {"layer", "smart"}:
        raise ValueError("装配步骤必须是 layer 或 smart")
    return result
