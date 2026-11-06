from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import BinaryIO

import h5py
import numpy as np
from PIL import Image
from scipy.ndimage import maximum_filter, minimum_filter


YCB_POINT_CLOUD_SCRIPT = (
    "https://ycb-benchmarks.s3.amazonaws.com/"
    "scripts_to_publish/ycb_generate_point_cloud.py"
)


def _member_bytes(archive: tarfile.TarFile, name: str) -> bytes:
    handle: BinaryIO | None = archive.extractfile(name)
    if handle is None:
        raise FileNotFoundError(f"压缩包成员不可读: {name}")
    return handle.read()


def _load_selected_members(
    archive_path: Path, selected_names: set[str]
) -> dict[str, bytes]:
    """顺序扫描 gzip tar，避免为每个成员重复解压整个大文件。"""
    payloads: dict[str, bytes] = {}
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            if member.name not in selected_names:
                continue
            payloads[member.name] = _member_bytes(archive, member.name)
            if len(payloads) == len(selected_names):
                break
    missing = selected_names - payloads.keys()
    if missing:
        raise FileNotFoundError(f"压缩包缺少成员: {', '.join(sorted(missing))}")
    return payloads


def _h5_array(payload: bytes, key: str) -> np.ndarray:
    with h5py.File(BytesIO(payload), "r") as handle:
        if key not in handle:
            raise KeyError(f"HDF5 中缺少数据集 {key}")
        return np.asarray(handle[key])


def _filter_discontinuities(
    raw_depth: np.ndarray,
    filter_size: int = 7,
    threshold: int = 1000,
) -> np.ndarray:
    """复现 YCB 官方脚本的深度不连续边缘过滤。"""
    if filter_size <= 0 or filter_size % 2 == 0:
        raise ValueError("filter_size 必须是正奇数")
    depth = np.asarray(raw_depth, dtype=np.uint16)
    local_min = minimum_filter(depth, size=filter_size, mode="constant", cval=0)
    local_max = maximum_filter(depth, size=filter_size, mode="constant", cval=0)
    discontinuity = np.maximum(
        np.abs(local_min.astype(np.int32) - depth.astype(np.int32)),
        np.abs(local_max.astype(np.int32) - depth.astype(np.int32)),
    )
    result = depth.copy()
    result[discontinuity > threshold] = 0
    return result


def register_depth_to_rgb(
    depth_m: np.ndarray,
    depth_intrinsic: np.ndarray,
    rgb_intrinsic: np.ndarray,
    rgb_from_depth: np.ndarray,
    rgb_shape: tuple[int, int],
) -> np.ndarray:
    """把深度相机中的米制深度注册到 RGB 像素平面，使用最近表面 z-buffer。"""
    depth = np.asarray(depth_m, dtype=np.float64)
    height, width = depth.shape
    rows, columns = np.indices((height, width))
    valid = np.isfinite(depth) & (depth > 0)
    if not valid.any():
        return np.zeros(rgb_shape, dtype=np.float32)

    z = depth[valid]
    u = columns[valid]
    v = rows[valid]
    x = (u - depth_intrinsic[0, 2]) * z / depth_intrinsic[0, 0]
    y = (v - depth_intrinsic[1, 2]) * z / depth_intrinsic[1, 1]
    points = np.stack((x, y, z), axis=1)
    transformed = points @ rgb_from_depth[:3, :3].T + rgb_from_depth[:3, 3]
    positive = transformed[:, 2] > 0
    transformed = transformed[positive]
    if len(transformed) == 0:
        return np.zeros(rgb_shape, dtype=np.float32)

    projected_u = np.rint(
        rgb_intrinsic[0, 0] * transformed[:, 0] / transformed[:, 2]
        + rgb_intrinsic[0, 2]
    ).astype(np.int64)
    projected_v = np.rint(
        rgb_intrinsic[1, 1] * transformed[:, 1] / transformed[:, 2]
        + rgb_intrinsic[1, 2]
    ).astype(np.int64)
    rgb_height, rgb_width = rgb_shape
    inside = (
        (projected_u >= 0)
        & (projected_u < rgb_width)
        & (projected_v >= 0)
        & (projected_v < rgb_height)
    )
    projected_u = projected_u[inside]
    projected_v = projected_v[inside]
    projected_z = transformed[inside, 2]

    flat = np.full(rgb_height * rgb_width, np.inf, dtype=np.float64)
    flat_indices = projected_v * rgb_width + projected_u
    np.minimum.at(flat, flat_indices, projected_z)
    flat[~np.isfinite(flat)] = 0
    return flat.reshape(rgb_shape).astype(np.float32)


def _object_foreground(mask_image: Image.Image) -> np.ndarray:
    """YCB PBM 的前景是较小区域；自动选择像素数更少的一侧。"""
    mask = np.asarray(mask_image.convert("1"), dtype=bool)
    true_count = int(mask.sum())
    false_count = int(mask.size - true_count)
    return mask if true_count <= false_count else ~mask


def _read_calibration(
    payload: bytes,
    camera: str,
    reference_camera: str,
) -> dict[str, object]:
    with h5py.File(BytesIO(payload), "r") as calibration:
        required = (
            f"{camera}_rgb_K",
            f"{camera}_ir_depth_scale",
            f"H_{camera}_from_{reference_camera}",
            f"H_{camera}_ir_from_{reference_camera}",
        )
        missing = [key for key in required if key not in calibration]
        if missing:
            raise KeyError(f"calibration.h5 缺少字段: {', '.join(missing)}")
        h_rgb_from_reference = np.asarray(
            calibration[f"H_{camera}_from_{reference_camera}"]
        )
        h_ir_from_reference = np.asarray(
            calibration[f"H_{camera}_ir_from_{reference_camera}"]
        )
        depth_key = (
            f"{camera}_depth_K"
            if f"{camera}_depth_K" in calibration
            else f"{camera}_ir_K"
        )
        if depth_key not in calibration:
            raise KeyError(
                f"calibration.h5 同时缺少 {camera}_depth_K 和 {camera}_ir_K"
            )
        return {
            # Berkeley 包中的原始 depth 数据是 640x480；*_depth_K 与该分辨率
            # 匹配，而 *_ir_K 通常对应约双倍分辨率的 IR 标定平面。
            "depth_intrinsic": np.asarray(calibration[depth_key]),
            "depth_intrinsic_key": depth_key,
            "rgb_intrinsic": np.asarray(calibration[f"{camera}_rgb_K"]),
            "raw_depth_scale_m": float(
                np.asarray(calibration[f"{camera}_ir_depth_scale"]).reshape(-1)[0]
            )
            * 0.0001,
            "rgb_from_depth": h_rgb_from_reference
            @ np.linalg.inv(h_ir_from_reference),
            "rgb_from_reference": h_rgb_from_reference,
        }


def prepare_ycb_rgbd(
    archive_path: str | Path,
    output_dir: str | Path,
    camera: str = "NP5",
    angle_step: int = 30,
    reference_camera: str = "NP5",
    depth_trunc_m: float = 1.5,
    use_masks: bool = True,
) -> Path:
    """从 YCB Berkeley RGB-D TGZ 生成已注册 PNG 和标准 LEGOLIZER manifest。"""
    if angle_step <= 0 or 360 % angle_step != 0:
        raise ValueError("angle_step 必须是 360 的正因数")
    archive_path = Path(archive_path).resolve()
    output = Path(output_dir).resolve()
    color_dir = output / "color"
    depth_dir = output / "depth"
    color_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    calibration_name = next(
        (name for name in names if name.endswith("/calibration.h5")), None
    )
    if calibration_name is None:
        raise FileNotFoundError("YCB 压缩包中没有 calibration.h5")
    root = PurePosixPath(calibration_name).parent.as_posix()
    frame_pattern = re.compile(
        rf"^{re.escape(root)}/([A-Za-z0-9]+)_(\d+)\.h5$"
    )
    available: dict[str, list[int]] = {}
    for name in names:
        match = frame_pattern.match(name)
        if match is not None:
            available.setdefault(match.group(1), []).append(int(match.group(2)))
    if camera.strip().lower() == "all":
        cameras = sorted(available)
    else:
        cameras = [item.strip() for item in camera.split(",") if item.strip()]
    if not cameras:
        raise ValueError("camera 不能为空")
    missing_cameras = [item for item in cameras if item not in available]
    if missing_cameras:
        raise ValueError(
            f"压缩包中没有相机: {', '.join(missing_cameras)}；"
            f"可用相机: {', '.join(sorted(available))}"
        )
    selected_frames = [
        (item, angle)
        for item in cameras
        for angle in sorted(available[item])
        if angle % angle_step == 0
    ]
    if not selected_frames:
        raise ValueError(f"压缩包中没有 angle_step={angle_step} 的帧")

    selected_names = {calibration_name}
    for frame_camera, angle in selected_frames:
        stem = f"{frame_camera}_{angle}"
        required = {
            f"{root}/{stem}.h5",
            f"{root}/{stem}.jpg",
            f"{root}/poses/{reference_camera}_{angle}_pose.h5",
        }
        selected_names.update(required)
        mask_name = f"{root}/masks/{stem}_mask.pbm"
        if use_masks and mask_name in names:
            selected_names.add(mask_name)
    payloads = _load_selected_members(archive_path, selected_names)
    calibrations = {
        item: _read_calibration(payloads[calibration_name], item, reference_camera)
        for item in cameras
    }

    frames: list[dict] = []
    frame_stats: list[dict] = []
    for frame_camera, angle in selected_frames:
        calibration = calibrations[frame_camera]
        rgb_intrinsic = np.asarray(calibration["rgb_intrinsic"])
        stem = f"{frame_camera}_{angle}"
        depth_name = f"{root}/{stem}.h5"
        color_name = f"{root}/{stem}.jpg"
        pose_name = f"{root}/poses/{reference_camera}_{angle}_pose.h5"
        mask_name = f"{root}/masks/{stem}_mask.pbm"

        raw_depth = _h5_array(payloads[depth_name], "depth")
        filtered_depth = _filter_discontinuities(raw_depth)
        depth_m = filtered_depth.astype(np.float64) * float(
            calibration["raw_depth_scale_m"]
        )
        color = np.asarray(
            Image.open(BytesIO(payloads[color_name])).convert("RGB")
        )
        registered_depth = register_depth_to_rgb(
            depth_m,
            np.asarray(calibration["depth_intrinsic"]),
            rgb_intrinsic,
            np.asarray(calibration["rgb_from_depth"]),
            color.shape[:2],
        )
        registered_before_mask = int(np.count_nonzero(registered_depth))
        foreground_pixels = color.shape[0] * color.shape[1]
        if use_masks and mask_name in payloads:
            foreground = _object_foreground(Image.open(BytesIO(payloads[mask_name])))
            if foreground.shape != registered_depth.shape:
                raise ValueError(f"{mask_name} 尺寸与 RGB 不一致")
            registered_depth[~foreground] = 0
            color = color.copy()
            color[~foreground] = 0
            foreground_pixels = int(foreground.sum())

        depth_mm = np.rint(registered_depth * 1000.0)
        depth_mm[(depth_mm < 0) | (depth_mm > np.iinfo(np.uint16).max)] = 0
        depth_mm = depth_mm.astype(np.uint16)
        color_path = color_dir / f"{stem}.png"
        depth_path = depth_dir / f"{stem}.png"
        Image.fromarray(color).save(color_path)
        Image.fromarray(depth_mm).save(depth_path)

        table_from_reference = _h5_array(
            payloads[pose_name], "H_table_from_reference_camera"
        )
        camera_to_world = table_from_reference @ np.linalg.inv(
            np.asarray(calibration["rgb_from_reference"])
        )
        frames.append(
            {
                "color": color_path.relative_to(output).as_posix(),
                "depth": depth_path.relative_to(output).as_posix(),
                "pose": camera_to_world.tolist(),
                "intrinsic": {
                    "width": int(color.shape[1]),
                    "height": int(color.shape[0]),
                    "fx": float(rgb_intrinsic[0, 0]),
                    "fy": float(rgb_intrinsic[1, 1]),
                    "cx": float(rgb_intrinsic[0, 2]),
                    "cy": float(rgb_intrinsic[1, 2]),
                },
                "camera": frame_camera,
                "turntable_angle_deg": angle,
            }
        )
        frame_stats.append(
            {
                "camera": frame_camera,
                "angle_deg": angle,
                "raw_valid_depth_pixels": int(np.count_nonzero(raw_depth)),
                "filtered_valid_depth_pixels": int(
                    np.count_nonzero(filtered_depth)
                ),
                "registered_depth_pixels_before_mask": registered_before_mask,
                "valid_registered_depth_pixels": int(np.count_nonzero(depth_mm)),
                "foreground_mask_pixels": foreground_pixels,
            }
        )

    valid_frame_count = sum(
        stat["valid_registered_depth_pixels"] > 0 for stat in frame_stats
    )
    if valid_frame_count == 0:
        raise ValueError(
            "所有帧在 RGB 配准和掩码后均为空；请检查相机名、标定和 mask"
        )

    first_color = np.asarray(Image.open(output / frames[0]["color"]))
    first_intrinsic = frames[0]["intrinsic"]
    manifest = {
        "dataset": "YCB Berkeley RGB-D",
        "source_archive": str(archive_path),
        "source_processing_reference": YCB_POINT_CLOUD_SCRIPT,
        "intrinsic": {
            "width": int(first_color.shape[1]),
            "height": int(first_color.shape[0]),
            "fx": first_intrinsic["fx"],
            "fy": first_intrinsic["fy"],
            "cx": first_intrinsic["cx"],
            "cy": first_intrinsic["cy"],
        },
        "pose_convention": "camera_to_world",
        "world_frame": "turntable",
        "depth_scale": 1000.0,
        "depth_trunc_m": depth_trunc_m,
        "convert_rgb_to_intensity": False,
        "frames": frames,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "prepare_report.json").write_text(
        json.dumps(
            {
                "archive": str(archive_path),
                "cameras": cameras,
                "reference_camera": reference_camera,
                "angle_step": angle_step,
                "calibrations": {
                    item: {
                        "raw_depth_unit_m": float(
                            calibrations[item]["raw_depth_scale_m"]
                        ),
                        "depth_intrinsic_key": calibrations[item][
                            "depth_intrinsic_key"
                        ],
                    }
                    for item in cameras
                },
                "registered_depth_unit": "millimetres in uint16 PNG",
                "use_masks": use_masks,
                "frames": frame_stats,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path
