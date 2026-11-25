"""Extract RGB points from OpenMask3D masks aligned to the original PLY vertex order."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import numpy as np
import open3d as o3d


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_scene(scene: Path) -> o3d.geometry.PointCloud:
    """Read points in source vertex order, retaining their colours."""
    cloud = o3d.io.read_point_cloud(str(scene))
    if not cloud.has_colors() or len(cloud.points) == 0 or not np.isfinite(np.asarray(cloud.points)).all():
        raise ValueError("场景 PLY 需要包含有限坐标和逐点 RGB")
    return cloud


def read_scene_instances(scene: Path, mask_file: Path) -> tuple[o3d.geometry.PointCloud, np.ndarray]:
    """Read a coloured scene and its N×K binary mask array, validating alignment metadata."""
    cloud = read_scene(scene)
    match mask_file.suffix.lower():
        case ".npy":
            masks = np.load(mask_file, allow_pickle=False, mmap_mode="r")
        case ".npz":
            with ZipFile(mask_file) as archive:
                if "masks.npy" not in archive.namelist() or archive.getinfo("masks.npy").file_size > 1024 ** 3:
                    raise ValueError("NPZ 需要包含 masks 数组，解压后的掩码大小上限为 1 GiB")
                if "scene_sha256.npy" in archive.namelist() and archive.getinfo("scene_sha256.npy").file_size > 4096:
                    raise ValueError("scene_sha256 元数据格式无效")
            with np.load(mask_file, allow_pickle=False) as archive:
                masks = archive["masks"]
                if "scene_sha256" in archive and archive["scene_sha256"].item() != _sha256(scene):
                    raise ValueError("掩码来源与当前场景 PLY 的 SHA-256 不一致")
        case _:
            raise ValueError("请使用 OpenMask3D 导出的 .npy 或 .npz 掩码")
    if masks.ndim != 2 or masks.shape[0] != len(cloud.points) or masks.shape[1] == 0:
        raise ValueError(f"掩码必须为 N×K，N 等于场景 PLY 的 {len(cloud.points)} 个点；当前形状为 {masks.shape}")
    if masks.dtype.kind not in "bifu":
        raise ValueError("掩码元素需要是布尔值或 0、1")
    if masks.dtype != np.bool_:
        for start in range(0, len(masks), 4096):
            block = masks[start:start + 4096]
            if not np.all((block == 0) | (block == 1)):
                raise ValueError("掩码需要是二值矩阵，不能使用特征或概率矩阵")
    return cloud, masks


def inspect_instances(scene: Path, mask_file: Path, point: int | None = None) -> dict:
    """List selectable masks, optionally restricting them to masks containing one picked vertex."""
    cloud, masks = read_scene_instances(scene, mask_file)
    sizes = np.count_nonzero(masks, axis=0)
    if point is not None and not 0 <= point < len(cloud.points):
        raise ValueError("选点超出当前场景的顶点编号")
    instances = [dict(id=int(i), points=int(sizes[i])) for i in np.argsort(-sizes, kind="stable")
                 if sizes[i] >= 10 and (point is None or masks[point, i])]
    return dict(scene_points=len(cloud.points), mask_count=masks.shape[1], instances=instances)


def extract_instance(scene: Path, mask_file: Path, instance: int, output: Path, point: int | None = None) -> dict:
    """Export one instance in source coordinates, with RGB copied unchanged."""
    cloud, masks = read_scene_instances(scene, mask_file)
    if not 0 <= instance < masks.shape[1]:
        raise ValueError("所选实例 ID 超出掩码范围")
    if point is not None and (not 0 <= point < len(cloud.points) or not masks[point, instance]):
        raise ValueError("所选实例不包含当前选点")
    indices = np.flatnonzero(masks[:, instance])
    if len(indices) < 10:
        raise ValueError("所选实例少于 10 个点，无法进入点云重建")
    if output.suffix.lower() != ".ply" or output.exists():
        raise ValueError("请选择尚不存在的 .ply 输出文件")
    selected = cloud.select_by_index(indices.tolist())
    output.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output), selected):
        raise RuntimeError(f"实例 PLY 写出失败：{output}")
    report = dict(method="openmask3d", scene=str(scene.resolve()), scene_sha256=_sha256(scene),
                  masks=str(mask_file.resolve()), masks_sha256=_sha256(mask_file), instance=instance, picked_point=point,
                  points=len(indices), path=str(output.resolve()))
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("masks", type=Path, nargs="?")
    parser.add_argument("--instance", type=int)
    parser.add_argument("--point", type=int)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.instance is None) != (args.output is None):
        parser.error("实例导出需要同时提供 --instance 和 --output")
    try:
        if args.preview:
            cloud = read_scene(args.scene)
            if not o3d.io.write_point_cloud(str(args.preview), cloud):
                raise RuntimeError("场景点云预览写出失败")
            result = dict(scene_points=len(cloud.points), path=str(args.preview.resolve()))
        elif args.masks is None:
            raise ValueError("请提供对应场景的实例掩码")
        else:
            result = (inspect_instances(args.scene, args.masks, args.point) if args.instance is None
                      else extract_instance(args.scene, args.masks, args.instance, args.output, args.point))
    except (ValueError, OSError, RuntimeError, BadZipFile) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
