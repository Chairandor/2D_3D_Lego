from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from legolizer.constants import DEFAULT_PROJECT_DIR

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legolizer")
    commands = parser.add_subparsers(dest="command", required=True)

    command = commands.add_parser("ui", help="启动本地交互转换工作台（LEGO 使用 legolization）")
    command.add_argument("--port", type=int, default=8765)
    command.add_argument("--no-open", action="store_true")

    command = commands.add_parser("mesh-to-pointcloud", help="Poisson Disk 表面采样")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("--points", type=int, default=50_000)
    command.add_argument("--seed", type=int, default=0)
    command.add_argument("--source-unit-mm", type=float, default=1.0)

    command = commands.add_parser("pointcloud-to-mesh", help="Poisson 或 Ball Pivoting 重建")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("--method", choices=("poisson", "ball-pivoting"), default="poisson")
    command.add_argument("--poisson-depth", type=int, default=8)
    command.add_argument("--density-quantile", type=float, default=0.01)
    command.add_argument("--normal-radius", type=float)
    command.add_argument("--source-unit-mm", type=float, default=1.0)

    command = commands.add_parser("mesh-to-voxel", help="Mesh 体素化")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument("--voxel-size", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    command.add_argument("--fill", choices=("surface", "holes", "orthographic"), default="orthographic")
    command.add_argument("--source-unit-mm", type=float, default=1.0)

    command = commands.add_parser("pointcloud-to-voxel", help="Point Cloud 直接体素化")
    command.add_argument("input")
    command.add_argument("output")
    command.add_argument(
        "--voxel-size",
        nargs=3,
        type=float,
        required=True,
        metavar=("X", "Y", "Z"),
    )
    command.add_argument("--source-unit-mm", type=float, default=1.0)

    command = commands.add_parser("voxel-to-mesh", help="Marching Cubes")
    command.add_argument("input")
    command.add_argument("output")

    command = commands.add_parser("rgbd-to-pointcloud", help="Open3D RGB-D 反投影")
    command.add_argument("manifest")
    command.add_argument("output")
    command.add_argument("--downsample-mm", type=float)

    command = commands.add_parser("rgbd-to-mesh", help="TSDF 或 Point Cloud→Poisson")
    command.add_argument("manifest")
    command.add_argument("output")
    command.add_argument("--method", choices=("tsdf", "poisson"), default="tsdf")
    command.add_argument("--voxel-length-m", type=float, default=0.004)
    command.add_argument("--sdf-trunc-m", type=float, default=0.02)
    command.add_argument("--poisson-depth", type=int, default=8)

    command = commands.add_parser("legoize", help="Mesh→Voxel→LEGO→LDraw/BOM/Steps")
    command.add_argument("input")
    command.add_argument("output_dir")
    command.add_argument("--target-width-studs", type=int, default=24)
    command.add_argument("--fill", choices=("surface", "holes", "orthographic"), default="orthographic")
    command.add_argument("--color", type=int, default=4)
    command.add_argument("--single-color", action="store_true")
    orientation_options = command.add_mutually_exclusive_group()
    orientation_options.add_argument(
        "--source-up-axis",
        choices=("auto", "x", "-x", "y", "-y", "z", "-z"),
        default="z",
        help="源模型朝上轴；auto 会评分，结果不明确时要求显式指定（默认: z）",
    )
    orientation_options.add_argument(
        "--source-frame", choices=("sf3d",),
        help="已知来源的姿态约定；SF3D 为 +Y 朝上、输入图像来自 -Z，可用大平面微调",
    )
    command.add_argument("--legolization-quality", choices=("fast", "balanced", "exhaustive", "direct"), default="fast")
    command.add_argument("--legolization-support", choices=("table", "baseplate"), default="table")
    command.add_argument("--legolization-hollow", action="store_true")
    command.add_argument("--legolization-duration-seconds", type=float)
    command.add_argument("--legolization-project-dir", default=str(DEFAULT_PROJECT_DIR))

    command = commands.add_parser("download-redwood", help="下载 Open3D Redwood 样例")
    command.add_argument("output_dir")

    command = commands.add_parser("prepare-ycb-rgbd", help="注册 YCB HDF5 深度并生成 manifest")
    command.add_argument("archive")
    command.add_argument("output_dir")
    command.add_argument(
        "--camera",
        default="NP5",
        help="相机名、逗号分隔的相机列表，或 all（默认: NP5）",
    )
    command.add_argument("--angle-step", type=int, default=30)
    command.add_argument("--depth-trunc-m", type=float, default=1.5)
    command.add_argument("--no-mask", action="store_true")

    command = commands.add_parser(
        "legalize",
        help="调用 legolization 生成布局、BOM 和装配审计 bundle",
    )
    command.add_argument("input", help="LEGOLIZER occupancy.npz")
    command.add_argument("output_dir")
    command.add_argument(
        "--project-dir", default=str(DEFAULT_PROJECT_DIR)
    )
    command.add_argument(
        "--quality", choices=("fast", "balanced", "exhaustive", "direct"), default="fast"
    )
    command.add_argument("--duration-seconds", type=float)
    command.add_argument("--support-mode", choices=("table", "baseplate"), default="table")
    command.add_argument("--hollow", action="store_true", help="显式开启严格后端空心化")

    command = commands.add_parser("preview", help="打开 STL/PLY/NPZ 的 Open3D 预览窗口")
    command.add_argument("input")
    orientation_options = command.add_mutually_exclusive_group()
    orientation_options.add_argument(
        "--up-axis",
        choices=("auto", "x", "-x", "y", "-y", "z", "-z"),
        default="z",
        help="仅调整预览方向；auto 会显示候选评分（默认: z）",
    )
    orientation_options.add_argument("--source-frame", choices=("sf3d",), help="按已知 SF3D 来源姿态预览 Mesh")
    command.add_argument("--info", action="store_true", help="只打印类型和尺寸，不打开窗口")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "ui":
        from legolizer.ui.launcher import main as ui_main

        ui_args = ["--port", str(args.port)]
        if args.no_open:
            ui_args.append("--no-open")
        return ui_main(ui_args)
    elif args.command == "mesh-to-pointcloud":
        from legolizer.processing3d.conversion import mesh_to_point_cloud

        result = mesh_to_point_cloud(
            args.input,
            args.output,
            args.points,
            args.seed,
            source_unit_mm=args.source_unit_mm,
        )
        print(json.dumps({"points": len(result.points), "output": str(Path(args.output).resolve())}))
    elif args.command == "pointcloud-to-mesh":
        from legolizer.processing3d.conversion import point_cloud_to_mesh

        mesh = point_cloud_to_mesh(
            args.input,
            args.output,
            method=args.method,
            poisson_depth=args.poisson_depth,
            density_quantile=args.density_quantile,
            normal_radius=args.normal_radius,
            source_unit_mm=args.source_unit_mm,
        )
        print(json.dumps({"vertices": len(mesh.vertices), "triangles": len(mesh.triangles)}))
    elif args.command == "mesh-to-voxel":
        from legolizer.processing3d.voxel import mesh_file_to_voxel_file

        volume = mesh_file_to_voxel_file(
            args.input,
            args.output,
            tuple(args.voxel_size),
            args.fill,
            source_unit_mm=args.source_unit_mm,
        )
        print(json.dumps({"shape": volume.occupancy.shape, "occupied": int(volume.occupancy.sum())}))
    elif args.command == "pointcloud-to-voxel":
        from legolizer.processing3d.voxel import point_cloud_file_to_voxel_file

        volume = point_cloud_file_to_voxel_file(
            args.input,
            args.output,
            tuple(args.voxel_size),
            source_unit_mm=args.source_unit_mm,
        )
        print(
            json.dumps(
                {
                    "shape": volume.occupancy.shape,
                    "occupied": int(volume.occupancy.sum()),
                    "colored": volume.colors is not None,
                }
            )
        )
    elif args.command == "voxel-to-mesh":
        from legolizer.processing3d.voxel import voxel_file_to_mesh_file

        voxel_file_to_mesh_file(args.input, args.output)
        print(json.dumps({"output": str(Path(args.output).resolve())}))
    elif args.command == "rgbd-to-pointcloud":
        from legolizer.processing3d.rgbd import rgbd_to_point_cloud

        cloud = rgbd_to_point_cloud(args.manifest, args.output, args.downsample_mm)
        print(json.dumps({"points": len(cloud.points)}))
    elif args.command == "rgbd-to-mesh":
        from legolizer.processing3d.rgbd import (
            rgbd_to_poisson_mesh,
            rgbd_to_tsdf_mesh,
        )

        if args.method == "tsdf":
            mesh = rgbd_to_tsdf_mesh(
                args.manifest, args.output, args.voxel_length_m, args.sdf_trunc_m
            )
        else:
            mesh = rgbd_to_poisson_mesh(
                args.manifest, args.output, poisson_depth=args.poisson_depth
            )
        print(json.dumps({"vertices": len(mesh.vertices), "triangles": len(mesh.triangles)}))
    elif args.command == "legoize":
        from legolizer.pipeline import legoize_mesh

        try:
            report = legoize_mesh(
                args.input,
                args.output_dir,
                target_width_studs=args.target_width_studs,
                fill=args.fill,
                color=args.color,
                use_source_colors=not args.single_color,
                source_up_axis=args.source_up_axis,
                source_frame=args.source_frame,
                legolization_quality=args.legolization_quality,
                legolization_support=args.legolization_support,
                legolization_hollow=args.legolization_hollow,
                legolization_project_dir=args.legolization_project_dir,
                legolization_duration_seconds=args.legolization_duration_seconds,
            )
        except ValueError as exc:
            raise SystemExit(f"legolizer legoize: {exc}") from None
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report["legolization"]["exit_code"])
    elif args.command == "download-redwood":
        from legolizer.processing3d.datasets import download_redwood

        manifest = download_redwood(args.output_dir)
        print(json.dumps({"manifest": str(manifest)}))
    elif args.command == "prepare-ycb-rgbd":
        from legolizer.processing3d.ycb import prepare_ycb_rgbd

        manifest = prepare_ycb_rgbd(
            args.archive,
            args.output_dir,
            camera=args.camera,
            angle_step=args.angle_step,
            depth_trunc_m=args.depth_trunc_m,
            use_masks=not args.no_mask,
        )
        print(json.dumps({"manifest": str(manifest)}))
    elif args.command == "legalize":
        from legolizer.integrations.backend import run_strict_backend
        from legolizer.processing3d.volume import VoxelVolume

        try:
            output = Path(args.output_dir)
            if output.exists() and any(output.iterdir()):
                raise ValueError(f"输出目录必须为空，避免混入旧产物：{output}")
            report = run_strict_backend(
                VoxelVolume.load(args.input), output,
                project_dir=args.project_dir, quality=args.quality,
                duration_seconds=args.duration_seconds,
                support_mode=args.support_mode, hollow=args.hollow,
            )
        except ValueError as exc:
            raise SystemExit(f"legolizer legalize: {exc}") from None
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report["exit_code"])
    elif args.command == "preview":
        from legolizer.processing3d.preview import preview_file

        try:
            info = preview_file(
                args.input, source_up_axis=args.up_axis, info_only=args.info,
                source_frame=args.source_frame,
            )
        except ValueError as exc:
            raise SystemExit(f"legolizer preview: {exc}") from None
        print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0
