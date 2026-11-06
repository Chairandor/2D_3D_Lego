"""UI 操作目录及参数校验；长度单位在每个字段中显式声明。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


def field(key: str, label: str, default: Any, *, choices: list | None = None,
          minimum: float | None = None, maximum: float | None = None,
          group: str = "转换参数") -> dict:
    return dict(key=key, label=label, default=default, choices=choices,
                minimum=minimum, maximum=maximum, group=group,
                type="bool" if isinstance(default, bool) else
                "int" if isinstance(default, int) else
                "float" if isinstance(default, float) else "str")


UNIT = field("source_unit_mm", "源坐标 1 单位 = 多少毫米", 1.0, minimum=1e-9, maximum=1e9)
POINTS = [field("points", "采样点数", 50000, minimum=100, maximum=2000000),
          field("seed", "随机种子", 0, minimum=0, maximum=2147483647)]
POISSON = [field("method", "重建方法", "poisson", choices=["poisson", "ball-pivoting"]),
           field("poisson_depth", "Poisson 深度", 8, minimum=4, maximum=12),
           field("density_quantile", "密度过滤分位数", 0.01, minimum=0.0, maximum=0.99)]
VOXEL = [field(k, label, default, minimum=0.0001, maximum=10000)
         for k, label, default in [("voxel_x", "体素 X / mm", 8.0),
                                    ("voxel_y", "体素 Y / mm", 8.0),
                                    ("voxel_z", "体素 Z / mm", 9.6)]]
FILL = field("fill", "体素填充", "orthographic", choices=["orthographic", "holes", "surface"])
LEGO = [
    field("target_width_studs", "目标宽度 / stud", 24, minimum=4, maximum=128, group="LEGO 设置"),
    field("orientation", "源上轴 / 已知来源", "z", choices=["z", "y", "x", "-z", "-y", "-x", "auto", "sf3d"], group="LEGO 设置"),
    field("single_color", "使用单色", False, group="LEGO 设置"),
    field("color", "备用 LDraw 色号", 4, minimum=0, maximum=10000, group="LEGO 设置"),
    field("quality", "严格后端质量", "fast", choices=["fast", "balanced", "direct"], group="严格后端"),
    field("support_mode", "严格后端支撑方式", "table", choices=["table", "baseplate"], group="严格后端"),
    field("hollow", "严格后端空心化", False, group="严格后端"),
    field("duration_seconds", "严格后端求解预算 / 秒", 60.0, minimum=1.0, maximum=3600.0, group="严格后端"),
]
RGBD = [field("rgbd_method", "重建方法", "tsdf", choices=["tsdf", "poisson"]),
        field("voxel_length_m", "TSDF 体素边长 / m", 0.004, minimum=0.0001, maximum=0.1),
        field("sdf_trunc_m", "TSDF 截断距离 / m", 0.02, minimum=0.0001, maximum=1.0),
        field("poisson_depth", "Poisson 深度", 8, minimum=4, maximum=12)]


def operation(key: str, label: str, family: str, accepts: str, stages: list[str],
              fields: list[dict], note: str) -> dict:
    return dict(id=key, label=label, family=family, accepts=accepts,
                stages=stages, fields=fields, note=note)


OPERATIONS = [
    operation("legoize", "Mesh → LEGO", "完整流水线", "mesh", ["LEGO 化与导出"], [FILL, *LEGO],
              "按目标 stud 宽度缩放，输出规范化 Mesh、体素、LDraw、BOM 和步骤。上轴必须与源文件一致。"),
    operation("pointcloud-lego", "点云 → Mesh → LEGO", "完整流水线", "pointcloud", ["点云表面重建", "LEGO 化与导出"], [UNIT, *POISSON, FILL, *LEGO],
              "先估计法向并重建表面，再 LEGO 化。源上轴用于第二阶段；单位换算只执行一次。"),
    operation("rgbd-lego", "RGB-D → Mesh → LEGO", "完整流水线", "rgbd", ["RGB-D 重建", "LEGO 化与导出"], [*RGBD, FILL, *LEGO],
              "需要含内参、位姿与图片路径的 manifest。重建后进入 LEGO；场景数据请先在外部裁剪目标。"),
    operation("mesh-to-pointcloud", "Mesh → 点云", "格式转换", "mesh", ["Poisson Disk 采样"], [UNIT, *POINTS],
              "表面采样输出 PLY 点云；固定种子可重复。源文件无单位时需手动填写单位倍率。"),
    operation("pointcloud-to-mesh", "点云 → Mesh", "格式转换", "pointcloud", ["点云表面重建"], [UNIT, *POISSON],
              "支持 Poisson 与 Ball Pivoting，输出毫米制 PLY 三角网格。"),
    operation("mesh-to-voxel", "Mesh → Voxel", "格式转换", "mesh", ["Mesh 体素化"], [UNIT, *VOXEL, FILL],
              "输出包含原点、体素尺寸与占用语义的 NPZ；不自动判断或更改上轴。"),
    operation("pointcloud-to-voxel", "点云 → Voxel", "格式转换", "pointcloud", ["点云体素化"], [UNIT, *VOXEL],
              "仅保留点命中的格子，不等同于填充实体；输出 NPZ。"),
    operation("voxel-to-mesh", "Voxel → Mesh", "格式转换", "voxel", ["Marching Cubes"], [],
              "读取项目 VoxelVolume NPZ，输出毫米制 PLY。粗体素无法恢复源模型细节。"),
    operation("rgbd-to-pointcloud", "RGB-D → 点云", "RGB-D 重建", "rgbd", ["RGB-D 反投影与融合"],
              [field("downsample_mm", "降采样边长 / mm", 4.0, minimum=0.1, maximum=100.0)],
              "输入 manifest 与本地 RGB-D 帧；深度尺度、位姿约定和内参由 manifest 决定。"),
    operation("rgbd-to-mesh", "RGB-D → Mesh", "RGB-D 重建", "rgbd", ["RGB-D 重建"], RGBD,
              "TSDF 输入是带内外参的 RGB-D 序列；也支持 RGB-D → 点云 → Poisson。"),
    operation("legalize", "Voxel → 严格 LEGO", "结构检查", "voxel", ["严格布局与 bundle"],
              [f for f in LEGO if f["key"] in {"quality", "support_mode", "duration_seconds", "hollow"}],
              "调用本地 legolization 隔离环境。partial 仍是部分结果，不能作为说明书通过。"),
    operation("prepare-ycb-rgbd", "YCB → RGB-D manifest", "RGB-D 重建", "archive", ["YCB 标定配准"],
              [field("camera", "相机", "NP5", choices=["NP1", "NP2", "NP3", "NP4", "NP5", "all"]),
               field("angle_step", "转台角度间隔 / °", 30, minimum=1, maximum=360),
               field("depth_trunc_m", "深度截断 / m", 1.5, minimum=0.01, maximum=10.0)],
              "使用已下载的 YCB TGZ，配准深度并保留目标 mask；不会自动下载数据。"),
]
BY_ID = {op["id"]: op for op in OPERATIONS}
SUFFIXES = {"mesh": {".ply", ".stl", ".obj", ".off", ".gltf", ".glb"},
            "pointcloud": {".ply", ".pcd", ".xyz", ".xyzn", ".xyzrgb", ".pts"},
            "voxel": {".npz"}, "rgbd": {".json"}, "archive": {".tgz", ".gz"}}


def validated_options(op: dict, supplied: dict) -> dict:
    """按白名单校验字段，拒绝非有限数及越界值。"""
    result = {}
    for spec in op["fields"]:
        value = supplied.get(spec["key"], spec["default"])
        try:
            if spec["type"] == "bool":
                if not isinstance(value, bool):
                    raise ValueError()
            elif spec["type"] in {"int", "float"}:
                numeric = float(value)
                if isinstance(value, bool) or not math.isfinite(numeric):
                    raise ValueError()
                if spec["type"] == "int" and not numeric.is_integer():
                    raise ValueError()
                value = int(numeric) if spec["type"] == "int" else numeric
                if spec["minimum"] is not None and value < spec["minimum"]:
                    raise ValueError()
                if spec["maximum"] is not None and value > spec["maximum"]:
                    raise ValueError()
            elif not isinstance(value, str):
                raise ValueError()
            if spec["choices"] and value not in spec["choices"]:
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"参数「{spec['label']}」无效，请检查取值范围") from None
        result[spec["key"]] = value
    return result


def build_stages(op_id: str, source: Path, output: Path, options: dict) -> list[dict]:
    """生成既有 CLI 的参数数组；不用 shell，不接受任意命令。"""
    op = BY_ID[op_id]
    if source.suffix.lower() not in SUFFIXES[op["accepts"]]:
        raise ValueError(f"输入格式与「{op['label']}」不符")
    if source.suffix.lower() == ".ply":
        with source.open("rb") as header:
            is_mesh = b"element face " in header.read(16384).split(b"end_header", 1)[0]
        if op["accepts"] == "mesh" and not is_mesh:
            raise ValueError("这个 PLY 是点云，请选择点云转换路径")
        if op["accepts"] == "pointcloud" and is_mesh:
            raise ValueError("这个 PLY 是 Mesh，请选择 Mesh 转换路径")
    v = validated_options(op, options)
    stages = []

    def add(command: str, src: Path, target: Path, flags: list[str]) -> None:
        stages.append(dict(label=op["stages"][len(stages)],
                           args=[command, str(src), str(target), *flags],
                           output=str(target), state="pending"))

    def flags(*keys: str) -> list[str]:
        return [value for k in keys if k in v for value in ("--" + k.replace("_", "-"), str(v[k]))]

    if op_id == "mesh-to-pointcloud":
        add(op_id, source, output / "points_mm.ply", flags("points", "seed", "source_unit_mm"))
    elif op_id in {"pointcloud-to-mesh", "pointcloud-lego"}:
        add("pointcloud-to-mesh", source, output / "reconstructed_mm.ply",
            flags("method", "poisson_depth", "density_quantile", "source_unit_mm"))
    elif op_id in {"mesh-to-voxel", "pointcloud-to-voxel"}:
        add(op_id, source, output / "occupancy.npz", flags("source_unit_mm", "fill") +
            ["--voxel-size", str(v["voxel_x"]), str(v["voxel_y"]), str(v["voxel_z"])])
    elif op_id == "voxel-to-mesh":
        add(op_id, source, output / "surface_mm.ply", [])
    elif op_id == "rgbd-to-pointcloud":
        add(op_id, source, output / "points_mm.ply", flags("downsample_mm"))
    elif op_id in {"rgbd-to-mesh", "rgbd-lego"}:
        add("rgbd-to-mesh", source, output / "reconstructed_mm.ply",
            ["--method", v["rgbd_method"], *flags("voxel_length_m", "sdf_trunc_m", "poisson_depth")])
    elif op_id == "legalize":
        add(op_id, source, output / "lego", flags("quality", "support_mode", "duration_seconds") + (["--hollow"] if v["hollow"] else []))
    elif op_id == "prepare-ycb-rgbd":
        add(op_id, source, output / "rgbd", flags("camera", "angle_step", "depth_trunc_m"))

    if op_id in {"legoize", "pointcloud-lego", "rgbd-lego"}:
        src = Path(stages[-1]["output"]) if stages else source
        extra = flags("target_width_studs", "color", "fill")
        extra += ["--source-frame=sf3d"] if v["orientation"] == "sf3d" else ["--source-up-axis=" + v["orientation"]]
        if v["single_color"]:
            extra.append("--single-color")
        if v["hollow"]:
            extra.append("--legolization-hollow")
        extra += ["--legolization-quality", v["quality"], "--legolization-support", v["support_mode"],
                  "--legolization-duration-seconds", str(v["duration_seconds"])]
        add("legoize", src, output / "lego", extra)
    return stages
