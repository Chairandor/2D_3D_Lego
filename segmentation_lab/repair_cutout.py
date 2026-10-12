"""Experimental automatic RGBA cutout repair candidate before 2D-to-3D.

Keep the selected segmentation and source image unchanged. This stage removes
only negligible detached mask islands, then uses a narrow image-matting band to
recover sub-pixel alpha and uncontaminated foreground colour. It does not
round the main silhouette or invent missing object parts. Controlled 3D trials
have not validated it for default pipeline use; keep it opt-in.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


def remove_negligible_islands(mask: np.ndarray) -> tuple[np.ndarray, dict[str, object]]:
    """Discard tiny detached components, never alter the main component."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    if count <= 2:
        return mask.copy(), {
            "components_before": count - 1,
            "removed_components": 0,
            "removed_pixels": 0,
        }
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    main_area = int(stats[main, cv2.CC_STAT_AREA])
    # Image-relative and capped: remove dust, not a substantial second subject.
    max_island = min(256, max(16, int(round(main_area * 0.00025))))
    result = mask.copy()
    removed_areas: list[int] = []
    for index in range(1, count):
        if index == main:
            continue
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area <= max_island:
            result[labels == index] = False
            removed_areas.append(area)
    return result, {
        "components_before": count - 1,
        "removed_components": len(removed_areas),
        "removed_pixels": int(sum(removed_areas)),
        "max_island_pixels": max_island,
        "retained_secondary_components": count - 2 - len(removed_areas),
    }


def repair(source: Image.Image, coarse: Image.Image) -> tuple[Image.Image, Image.Image, dict]:
    from rembg.bg import alpha_matting_cutout

    source_rgba = np.asarray(source.convert("RGBA"))
    source_rgb = source_rgba[:, :, :3]
    coarse_rgba = np.asarray(coarse.convert("RGBA"))
    if source_rgb.shape[:2] != coarse_rgba.shape[:2]:
        raise ValueError("source and cutout dimensions must match")
    original_mask = coarse_rgba[:, :, 3] >= 128
    if not original_mask.any():
        raise ValueError("empty foreground mask")
    cleaned_mask, island_report = remove_negligible_islands(original_mask)

    # A previously cut-out source no longer contains the real background
    # colours needed to unmix its edge. Preserve its existing alpha instead of
    # inventing a fringe against the transparent pixels' arbitrary RGB values.
    if np.any(source_rgba[:, :, 3] < 255):
        result = coarse_rgba.copy()
        removed = original_mask & ~cleaned_mask
        result[removed] = 0
        overlay = source_rgb.copy().astype(np.float32)
        overlay[removed] = 0.4 * overlay[removed] + 0.6 * np.array([255, 60, 0])
        report = {
            **island_report,
            "method": "existing_alpha_preserved_plus_tiny_islands",
            "binary_edge_changed_pixels": 0,
            "soft_alpha_pixels": int(np.count_nonzero((result[:, :, 3] > 0) & (result[:, :, 3] < 255))),
            "matting_fallback_to_binary": False,
        }
        return (
            Image.fromarray(result, "RGBA"),
            Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8), "RGB"),
            report,
        )

    rows, cols = np.nonzero(cleaned_mask)
    height, width = cleaned_mask.shape
    padding = max(16, int(round(min(height, width) * 0.025)))
    y0, y1 = max(0, rows.min() - padding), min(height, rows.max() + padding + 1)
    x0, x1 = max(0, cols.min() - padding), min(width, cols.max() + padding + 1)
    source_crop = Image.fromarray(source_rgb[y0:y1, x0:x1], "RGB")
    mask_crop = Image.fromarray(cleaned_mask[y0:y1, x0:x1].astype(np.uint8) * 255, "L")
    matte = np.asarray(alpha_matting_cutout(source_crop, mask_crop, 240, 10, 3))

    result = np.zeros((height, width, 4), dtype=np.uint8)
    result[y0:y1, x0:x1] = matte
    # The 3x3 trimap only permits changes at the selected mask's immediate
    # boundary. Force all certain interior/exterior pixels back to exact values.
    kernel = np.ones((3, 3), dtype=np.uint8)
    sure_foreground = cv2.erode(cleaned_mask.astype(np.uint8), kernel) > 0
    sure_background = cv2.erode((~cleaned_mask).astype(np.uint8), kernel) > 0
    result[sure_foreground, 3] = 255
    result[sure_background, 3] = 0
    result[sure_foreground, :3] = source_rgb[sure_foreground]
    result[result[:, :, 3] == 0, :3] = 0

    # A numerical guard keeps this an edge repair, not a silent re-segmentation.
    binary_difference = int(np.count_nonzero((result[:, :, 3] >= 128) != cleaned_mask))
    change_limit = max(256, int(original_mask.sum() * 0.02))
    fallback = binary_difference > change_limit
    if fallback:
        result = np.dstack((source_rgb, cleaned_mask.astype(np.uint8) * 255))
        result[~cleaned_mask, :3] = 0

    overlay = source_rgb.copy().astype(np.float32)
    removed = original_mask & ~cleaned_mask
    altered = (result[:, :, 3] >= 128) != cleaned_mask
    overlay[removed] = 0.4 * overlay[removed] + 0.6 * np.array([255, 60, 0])
    overlay[altered] = 0.4 * overlay[altered] + 0.6 * np.array([0, 180, 255])
    overlay_image = Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8), "RGB")
    report = {
        **island_report,
        "method": "tiny_islands_plus_narrow_trimap_matting",
        "matting_crop": [int(x0), int(y0), int(x1), int(y1)],
        "binary_edge_changed_pixels": binary_difference,
        "soft_alpha_pixels": int(np.count_nonzero((result[:, :, 3] > 0) & (result[:, :, 3] < 255))),
        "matting_fallback_to_binary": fallback,
    }
    return Image.fromarray(result, "RGBA"), overlay_image, report


def make_comparison(source: Image.Image, coarse: Image.Image, repaired: Image.Image) -> Image.Image:
    panel_size = (480, 520)
    sheet = Image.new("RGB", (panel_size[0] * 3, panel_size[1]), "#e7e9eb")
    for index, (label, image) in enumerate((
        ("ORIGINAL", source.convert("RGBA")),
        ("SAM 2 CUTOUT", coarse.convert("RGBA")),
        ("REPAIRED CUTOUT", repaired),
    )):
        fitted = ImageOps.contain(image, (460, 460))
        background = Image.new("RGBA", fitted.size, "#e7e9eb")
        background.alpha_composite(fitted)
        x = index * panel_size[0] + (panel_size[0] - fitted.width) // 2
        y = 44 + (460 - fitted.height) // 2
        sheet.paste(background.convert("RGB"), (x, y))
        ImageDraw.Draw(sheet).text((index * panel_size[0] + 12, 12), label, fill="#202020")
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair a selected RGBA cutout automatically")
    parser.add_argument("source", type=Path, help="original image before background removal")
    parser.add_argument("cutout", type=Path, help="selected coarse RGBA cutout")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"Output directory is not empty: {args.output_dir}")
    with Image.open(args.source) as source, Image.open(args.cutout) as coarse:
        repaired, overlay, report = repair(source, coarse)
        comparison = make_comparison(source, coarse, repaired)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    repaired.save(args.output_dir / "foreground.png")
    Image.fromarray(np.asarray(repaired)[:, :, 3], "L").save(args.output_dir / "mask.png")
    overlay.save(args.output_dir / "repair_overlay.png")
    comparison.save(args.output_dir / "comparison.png")
    report.update({"source": str(args.source.resolve()), "cutout": str(args.cutout.resolve())})
    report["experimental_not_pipeline_default"] = True
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
