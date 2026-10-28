"""Immutable six-view silhouettes and pixel-exact colour-edit composition."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

import ui_revisions

POLICY = "six-view-colour-only/v1"
RULES = (
    "六视图只能修正主体表面颜色。最初版本的六个主体蒙版永久固定，"
    "任何请求都不得改变轮廓，连一个像素也不允许移动、增删或缩放。"
    "不得增删部件、改姿势、视角、布局、背景、透明度或边缘抗锯齿。"
    "只执行用户指定部位的颜色修正，其他部位保持原样；各视角同一部位颜色一致。"
    "不要重设计主体、重打光、添加纹理、文字或水印。"
    "如果要求涉及轮廓或几何变化，应说明六视图阶段不能执行，不要调用编辑工具。"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != "PNG":
                raise ValueError("六视图必须是 PNG 图片")
            return image.copy()
    except OSError as exc:
        raise ValueError("六视图图片无效，已拒绝修改") from exc


def _editable_mask(mask: Image.Image) -> Image.Image:
    # Only fully opaque interior pixels can change. The original antialiasing
    # band and a one-pixel inner border are immutable, even for RGB sheets.
    editable = mask.point(lambda value: 255 if value == 255 else 0).filter(ImageFilter.MinFilter(3))
    width, height = mask.size
    for left, top, right, bottom in (
        (0, 0, width, 1), (0, height - 1, width, height),
        (0, 0, 1, height), (width - 1, 0, width, height),
        (width // 3 - 1, 0, width // 3 + 1, height),
        (2 * width // 3 - 1, 0, 2 * width // 3 + 1, height),
        (0, height // 2 - 1, width, height // 2 + 1),
    ):
        editable.paste(0, (max(0, left), max(0, top), min(width, right), min(height, bottom)))
    if editable.getbbox() is None:
        raise ValueError("原始蒙版没有可安全修改的内部区域")
    return editable


@dataclass(frozen=True)
class SixViewReference:
    original: Image.Image
    mask: Image.Image
    source_run: Path
    image_sha256: str
    mask_sha256: str

    def lock(self, proposed: Image.Image) -> tuple[Image.Image, dict]:
        if proposed.size != self.original.size:
            raise ValueError(f"六视图尺寸必须保持 {self.original.size}，不能缩放或重新排版")
        editable = _editable_mask(self.mask)
        protected = ImageChops.invert(editable)
        original_rgb = self.original.convert("RGB")
        # Keep the earliest image's background and boundary pixels, never the
        # previous AI output's background, returned alpha, or a new segmentation.
        result = Image.composite(proposed.convert("RGB"), original_rgb, editable)
        if "A" in self.original.getbands():
            result = result.convert("RGBA")
            result.putalpha(self.original.getchannel("A"))
        difference = ImageChops.difference(result.convert("RGB"), original_rgb)
        locked_difference = Image.composite(difference, Image.new("RGB", result.size), protected)
        if locked_difference.getbbox() is not None:
            raise RuntimeError("六视图轮廓或背景校验失败，已拒绝修改")
        if "A" in self.original.getbands() and ImageChops.difference(
            result.getchannel("A"), self.original.getchannel("A")
        ).getbbox() is not None:
            raise RuntimeError("六视图透明度校验失败，已拒绝修改")
        return result, {
            "policy": POLICY, "source_run_dir": str(self.source_run),
            "baseline_image_sha256": self.image_sha256,
            "baseline_mask_sha256": self.mask_sha256,
            "size": list(result.size), "protected_pixels_changed": 0,
            "alpha_pixels_changed": 0,
            "editable_pixels": editable.histogram()[255],
        }


def _source_run(run_dir: Path, root: Path) -> Path:
    current = run_dir.resolve()
    root = root.resolve()
    seen: set[Path] = set()
    for _ in range(32):
        if current in seen or not current.is_relative_to(root):
            break
        seen.add(current)
        stage = ui_revisions.branch_start_stage(current, root)
        if stage != "texture":
            if (current / "unitex/mv_rgb.png").is_file():
                return current
            # A new input/geometry has its own silhouette. Never borrow the
            # previous geometry's mask just because it exists in an ancestor.
            if stage == "input":
                break
        parent = ui_revisions.branch_parent(current, root)
        if parent is None:
            break
        current = parent
    raise ValueError("找不到最初版本的六视图，已拒绝修改")


def resolve_reference(run_dir: Path, root: Path) -> SixViewReference:
    """Freeze the native sheet/mask once; all texture descendants share it."""
    owner = _source_run(run_dir, root)
    directory = owner / ui_revisions.REVISION_DIR / "six_view_reference"
    directory.mkdir(parents=True, exist_ok=True)
    metadata_path = directory / "reference.json"
    with ui_revisions._state_file_lock(metadata_path):
        if not metadata_path.is_file():
            image_data = (owner / "unitex/mv_rgb.png").read_bytes()
            mask_path = owner / "unitex/mv_alpha.png"
            if not mask_path.is_file():
                raise ValueError("缺少最初六视图的主体蒙版 mv_alpha.png，已拒绝修改；不会猜测或重新分割")
            mask_data = mask_path.read_bytes()
            original, mask = _load_image(image_data), _load_image(mask_data).convert("L")
            if original.size != mask.size or mask.getbbox() is None:
                raise ValueError("原始六视图与主体蒙版不匹配，已拒绝修改")
            _editable_mask(mask)
            (directory / "original.png").write_bytes(image_data)
            (directory / "mask.png").write_bytes(mask_data)
            metadata = {"policy": POLICY, "image_sha256": _sha(image_data), "mask_sha256": _sha(mask_data)}
            temporary = metadata_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            temporary.replace(metadata_path)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        image_data = (directory / "original.png").read_bytes()
        mask_data = (directory / "mask.png").read_bytes()
        if (metadata.get("policy") != POLICY or _sha(image_data) != metadata.get("image_sha256")
                or _sha(mask_data) != metadata.get("mask_sha256")):
            raise ValueError("固定六视图蒙版或原图已发生变化，已拒绝修改")
    original, mask = _load_image(image_data), _load_image(mask_data).convert("L")
    if original.size != mask.size:
        raise ValueError("固定六视图与蒙版尺寸不匹配")
    return SixViewReference(original, mask, owner, metadata["image_sha256"], metadata["mask_sha256"])


def constrain_candidate(run_dir: Path, content: bytes, root: Path) -> tuple[bytes, dict]:
    reference = resolve_reference(run_dir, root)
    result, report = reference.lock(_load_image(content))
    stream = io.BytesIO()
    result.save(stream, format="PNG")
    return stream.getvalue(), report


def validate_candidate(run_dir: Path, content: bytes, root: Path) -> dict:
    """Check adoption too, including old candidates produced before this policy."""
    proposed = _load_image(content)
    locked, report = resolve_reference(run_dir, root).lock(proposed)
    rgb_changed = ImageChops.difference(proposed.convert("RGB"), locked.convert("RGB")).getbbox()
    alpha_changed = ImageChops.difference(
        proposed.convert("RGBA").getchannel("A"), locked.convert("RGBA").getchannel("A")
    ).getbbox()
    if rgb_changed is not None or alpha_changed is not None:
        raise ValueError("此六视图候选不符合固定轮廓规则，请重新生成或上传颜色修改版")
    return report
