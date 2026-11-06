"""三条可追溯文件流水线：STL、单色点云、彩色点云 → legolization。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import secrets
import shutil
import time
import traceback

import numpy as np
import open3d as o3d

from legolizer.constants import DEFAULT_PROJECT_DIR, STUD_PITCH_MM, BRICK_HEIGHT_MM
from legolizer.integrations.backend import run_strict_backend
from legolizer.processing3d.conversion import point_cloud_to_mesh
from legolizer.processing3d.mesh import cleanup_mesh, mesh_stats, normalize_mesh, read_mesh, write_mesh
from legolizer.processing3d.orientation import orientation_matrix, score_mesh_orientations
from legolizer.processing3d.colors import COMMON_LDRAW_COLORS
from legolizer.processing3d.texturing import export_preview_glb, export_texture_bundle, transfer_point_colors
from legolizer.processing3d.voxel import mesh_to_voxel


from legolizer.processing3d.flow_config import FLOWS, validate_options


BRICK_VOXEL_MM = (STUD_PITCH_MM, STUD_PITCH_MM, BRICK_HEIGHT_MM)


def atomic_json(path: Path, value: dict) -> None:
    """用唯一临时文件和有限重试写状态，兼容 Windows 文件扫描锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{secrets.token_hex(6)}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)




def _cloud_stats(cloud: o3d.geometry.PointCloud) -> dict:
    points = np.asarray(cloud.points)
    if len(points) < 10 or not np.isfinite(points).all():
        raise ValueError("点云需要至少 10 个有限点")
    return dict(points=len(points), has_colors=cloud.has_colors(), has_normals=cloud.has_normals(),
                extent_mm=np.ptp(points, axis=0).tolist(), finite=True)


def run_pipeline(source: Path, output: Path, flow: str, options: dict,
                 *, prepare_only: bool = False, project_dir: Path = DEFAULT_PROJECT_DIR) -> dict:
    """处理原文件副本、输出各阶段预览、颜色资产、体素和后端审计。"""
    options = validate_options(flow, options)
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        raise ValueError(f"输入不存在：{source}")
    if source.suffix.lower() != (".stl" if flow == "stl" else ".ply"):
        raise ValueError("文件格式与所选转换路径不符")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"输出目录必须为空：{output}")
    if flow != "stl":
        with source.open("rb") as stream:
            header = stream.read(16384).split(b"end_header", 1)[0]
        if b"element face " in header and b"element face 0" not in header:
            raise ValueError("这份 PLY 是 Mesh，请选择点云 PLY；当前两条 PLY 路径用于点云重建")
    output.mkdir(parents=True, exist_ok=True)
    stages = [dict(id="source", label="原始 STL" if flow == "stl" else "原始点云", status="pending"),
              dict(id="mesh", label="网格清理" if flow == "stl" else "法向与表面重建", status="pending")]
    if flow == "ply_color":
        stages.append(dict(id="texture", label="颜色传递与 UV 贴图", status="pending"))
    stages.extend([dict(id="voxel", label="定尺与彩色体素", status="pending"),
                   dict(id="lego", label="legolization 求解", status="pending"),
                   dict(id="assembly", label="装配步骤", status="pending")])
    state = dict(version=1, flow=flow, title=FLOWS[flow]["label"], source=str(source),
                 source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), options=options,
                 status="running", started=time.time(), ended=None, stages=stages, assets=[],
                 report={}, output=str(output), message="读取输入", random_seed=0,
                 versions=dict(python=platform.python_version(), open3d=o3d.__version__, numpy=np.__version__))

    def save():
        atomic_json(output / "pipeline3d.json", state)

    def stage(key, status="running", **detail):
        row = next(s for s in stages if s["id"] == key)
        row.update(status=status, **detail)
        state["message"] = row["label"]
        save()
        print(f"[{key}] {status}: {row['label']}", flush=True)

    def asset(key, label, path, kind, stage_id, **detail):
        state["assets"].append(dict(id=key, label=label, path=str(path), kind=kind, stage=stage_id, **detail))
        save()

    try:
        stage("source")
        original = output / "source" / ("original" + source.suffix.lower())
        original.parent.mkdir(parents=True)
        shutil.copy2(source, original)
        asset("original", "原文件下载", original, "file", "source")
        unit = options["source_unit_mm"]
        axis = options["source_up_axis"]
        orientation = {"selection_source": "user_manual", "selected_axis": axis}
        if flow == "stl":
            raw = read_mesh(original)
            if axis == "auto":
                orientation = score_mesh_orientations(raw)
                if orientation["ambiguous"]:
                    raise ValueError("无法可靠自动确定朝向，请手动选择源上轴")
                axis = orientation["selected_axis"]
                orientation["selection_source"] = "geometry_auto"
            source_matrix = orientation_matrix(axis)
            source_matrix[:3, :3] *= unit
            raw.transform(source_matrix)
            stats = mesh_stats(raw)
            preview = export_preview_glb(raw, output / "source" / "original_preview.glb")
            asset("source", "原始 STL", preview, "mesh", "source", stats=stats,
                  note="原始几何，仅换算单位和显示上轴；原文件独立保留")
        else:
            raw = o3d.io.read_point_cloud(str(original))
            stats = _cloud_stats(raw)
            if flow == "ply_color" and not raw.has_colors():
                raise ValueError("有色 PLY 路径需要 RGB；这份点云没有颜色，请选择无色路径")
            source_matrix = orientation_matrix(axis)
            source_matrix[:3, :3] *= unit
            raw.transform(source_matrix)
            if flow == "ply_plain":
                raw.colors = o3d.utility.Vector3dVector(np.empty((0, 3)))
            stats = {**_cloud_stats(raw), "source_has_colors": stats["has_colors"],
                     "color_policy": "preserve" if flow == "ply_color" else "ignore"}
            points_path = output / "source" / "points_mm.ply"
            if not o3d.io.write_point_cloud(str(points_path), raw):
                raise RuntimeError("点云副本写出失败")
            asset("source", "原始彩色点云" if flow == "ply_color" else "原始点云（单色路径）",
                  points_path, "points", "source", stats=stats,
                  note="此路径忽略原文件 RGB" if flow == "ply_plain" and stats["source_has_colors"] else "保留输入 RGB" if flow == "ply_color" else "无色点云")
        state["report"]["source"] = stats
        transform_info = dict(source_to_processing=source_matrix.tolist(), source_up_axis=axis,
                              source_unit_mm=unit, unit="mm", up_axis="z", orientation=orientation,
                              color_policy=stats.get("color_policy", "single"))
        state["coordinates"] = transform_info
        atomic_json(output / "transform.json", transform_info)
        stage("source", "complete", stats=stats)
        stage("mesh")
        mesh_dir = output / "mesh"
        mesh_dir.mkdir()
        mesh_path = mesh_dir / "reconstructed_mm.ply"
        if flow == "stl":
            mesh = o3d.geometry.TriangleMesh(raw)
            cleanup_mesh(mesh)
            write_mesh(mesh_path, mesh)
        else:
            o3d.utility.random.seed(0)
            normal_radius = max(float(np.linalg.norm(raw.get_axis_aligned_bounding_box().get_extent())) * .02,
                                np.finfo(float).eps)
            state["report"]["reconstruction"] = dict(method="poisson", input_points=len(raw.points),
                normal_radius_mm=normal_radius, normal_max_nn=30,
                normal_consistency_neighbors=min(50, len(raw.points) - 1),
                poisson_depth=options["poisson_depth"], density_quantile=options["density_quantile"],
                bounding_box_crop_factor=1.05)
            mesh = point_cloud_to_mesh(points_path, mesh_path,
                                       normal_radius=normal_radius, normal_max_nn=30,
                                       poisson_depth=options["poisson_depth"],
                                       density_quantile=options["density_quantile"])
            if flow == "ply_color":
                state["report"]["color_transfer"] = transfer_point_colors(mesh, raw)
            else:
                mesh.vertex_colors = o3d.utility.Vector3dVector(np.empty((0, 3)))
            write_mesh(mesh_path, mesh)
        mesh_info = mesh_stats(mesh)
        mesh_view = export_preview_glb(mesh, mesh_dir / "mesh_preview.glb")
        write_mesh(mesh_dir / "mesh_mm.stl", mesh)
        asset("mesh", "彩色重建 Mesh" if flow == "ply_color" else "处理后 Mesh", mesh_view, "mesh", "mesh", stats=mesh_info)
        asset("mesh_ply", "Mesh PLY 下载", mesh_path, "file", "mesh")
        asset("mesh_stl", "Mesh STL 下载（不含颜色）", mesh_dir / "mesh_mm.stl", "file", "mesh")
        state["report"]["mesh"] = mesh_info
        stage("mesh", "complete", stats=mesh_info)
        if flow == "ply_color":
            stage("texture")
            texture_dir = output / "texture"
            texture_info = export_texture_bundle(mesh, texture_dir)
            state["report"]["texture"] = texture_info
            asset("texture", "UV 贴图 Mesh", texture_dir / texture_info["glb"], "mesh", "texture",
                  note=texture_info["note"], stats={"atlas": texture_info["atlas_size"]})
            asset("atlas", "点云 RGB 烘焙贴图", texture_dir / texture_info["texture"], "image", "texture")
            asset("obj", "带贴图 OBJ 资源包", texture_dir / texture_info["archive"], "file", "texture")
            stage("texture", "complete", stats=texture_info)
        if prepare_only:
            state.update(status="prepared", ended=time.time(), message="几何已准备，可开始求解 LEGO")
            save()
            return state
        stage("voxel")
        normalized, transform = normalize_mesh(mesh, options["target_width_studs"] * STUD_PITCH_MM)
        write_mesh(output / "normalized_mesh.ply", normalized)
        volume = mesh_to_voxel(normalized, BRICK_VOXEL_MM, fill="orthographic", source=str(mesh_path))
        volume.save(output / "source_occupancy.npz")
        palette = {c.code: c.rgb for c in COMMON_LDRAW_COLORS}
        occupied = np.argwhere(volume.occupancy)
        codes = volume.colors[volume.occupancy] if volume.colors is not None else np.full(len(occupied), options["color"])
        voxel_view = dict(centers=(np.asarray(volume.origin_mm) + (occupied + .5) * np.asarray(volume.voxel_size_mm)).tolist(),
                          size=list(volume.voxel_size_mm), colors=[palette.get(int(c), (160, 165, 169)) for c in codes])
        atomic_json(output / "voxel_preview.json", voxel_view)
        transform_info = dict(transform_info, source_to_normalized=(transform @ source_matrix).tolist(),
                              target_width_studs=options["target_width_studs"], voxel_size_mm=list(BRICK_VOXEL_MM))
        state["coordinates"] = transform_info
        atomic_json(output / "transform.json", transform_info)
        asset("voxel", "求解输入体素", output / "voxel_preview.json", "voxel", "voxel",
              stats={"占用格": len(occupied), "颜色数": len(np.unique(codes))}, note="实体占用体素；不是砖块布局")
        asset("voxel_npz", "体素 NPZ 下载", output / "source_occupancy.npz", "file", "voxel")
        state["report"]["voxel"] = dict(shape=list(volume.occupancy.shape), occupied_cells=len(occupied),
                                               color_count=len(np.unique(codes)), semantic=volume.semantic)
        stage("voxel", "complete", stats=state["report"]["voxel"])
        stage("lego")
        summary = run_strict_backend(volume, output / "solver", quality=options["quality"], support_mode="table",
                                     hollow=False, project_dir=project_dir, duration_seconds=options["duration_seconds"],
                                     color=options["color"],
                                     placement_strategy="fast" if options["quality"] == "direct" else None,
                                     instruction_mode=options["instruction_mode"])
        state["report"]["legolization"] = summary
        if summary["model_path"]:
            model = Path(summary["model_path"])
            asset("lego", "legolization LEGO", model, "lego", "lego", stats={
                "零件": summary["brick_count"], "步骤": summary["instruction_step_count"]},
                note="装配判定以原始报告为准")
        else:
            candidates = list((output / "solver/legolization/work").glob("pending/*/model.ldr"))
            if candidates:
                candidate = candidates[0]
                asset("lego_candidate", "未发布候选（未认证）", candidate, "lego", "lego",
                      note="后端未发布合法成品；仅供检查，不代表可拼或稳定")
        asset("bundle", "后端原始报告", Path(summary["bundle_path"]), "file", "lego")
        for key, relative, label in (("bom", "bom/bom.json", "BOM 零件清单"),
                                     ("audit", "instructions/audit.json", "装配审计报告")):
            path = Path(summary["bundle_path"]).parent / relative
            if path.is_file():
                asset(key, label, path, "file", "assembly")
        stage("lego", "complete" if summary["bundle_status"] == "complete" else "partial", stats=summary)
        stage("assembly", "complete" if summary["instructions_status"] == "complete" and summary["instruction_audit_valid"] is True else "partial",
              stats={"steps": summary["instruction_step_count"], "audit_valid": summary["instruction_audit_valid"],
                     "instructions_status": summary["instructions_status"]})
        state.update(status="complete" if summary["bundle_status"] == "complete" else "partial",
                     ended=time.time(), message="求解完成" if summary["bundle_status"] == "complete" else "已有部分结果，请检查装配判定")
        save()
        return state
    except Exception as exc:
        for row in stages:
            if row["status"] == "running":
                row.update(status="failed", error=str(exc))
        state.update(status="failed", ended=time.time(), message=str(exc))
        save()
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flow", choices=FLOWS, required=True)
    parser.add_argument("--options", default="{}")
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        result = run_pipeline(args.source, args.output, args.flow, json.loads(args.options),
                              prepare_only=args.prepare_only, project_dir=args.project_dir)
        print(json.dumps(dict(status=result["status"], output=str(args.output)), ensure_ascii=False))
        return 0 if result["status"] in {"complete", "prepared"} else int(result["report"].get("legolization", {}).get("exit_code", 3))
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
