"""LEGO-oriented foreground extraction before single-image 3D generation.

The segmentation model supplies a first proposal.  On backgrounds whose
border has a consistent chromaticity, an independent colour/brightness
proposal can rescue missed appendages and reject dark cast shadows.  A
review image and JSON report make the choice inspectable before 3D work.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw


def _background_proposal(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Return (salient object pixels, shadow pixels, border spread)."""
    height, width = rgb.shape[:2]
    stripe = max(4, min(height, width) // 48)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    border = np.concatenate(
        (
            lab[:stripe].reshape(-1, 3),
            lab[-stripe:].reshape(-1, 3),
            lab[:, :stripe].reshape(-1, 3),
            lab[:, -stripe:].reshape(-1, 3),
        ),
        axis=0,
    )
    border_ab = np.median(border[:, 1:3], axis=0)
    spread = float(np.percentile(np.linalg.norm(border[:, 1:3] - border_ab, axis=1), 90))
    if spread > 12.0:
        return None

    # Per-row border samples accommodate a smooth studio backdrop gradient.
    edge_rgb = np.concatenate((rgb[:, :stripe], rgb[:, -stripe:]), axis=1)
    row_rgb = np.median(edge_rgb, axis=1).astype(np.uint8)
    row_lab = cv2.cvtColor(row_rgb[:, None, :], cv2.COLOR_RGB2LAB).astype(np.float32)
    chroma_delta = np.linalg.norm(lab[:, :, 1:3] - row_lab[:, :, 1:3], axis=2)
    light_delta = lab[:, :, 0] - row_lab[:, :, 0]
    salient = (chroma_delta > 16.0) | (light_delta > 20.0)
    shadow = (chroma_delta < 12.0) & (light_delta < -24.0)
    return salient, shadow, spread


def _fill_small_model_holes(mask: np.ndarray, model_mask: np.ndarray) -> np.ndarray:
    """Restore small enclosed dark details without filling open object gaps."""
    height, width = mask.shape
    background_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (~mask).astype(np.uint8), connectivity=8
    )
    max_area = max(256, int(height * width * 0.004))
    for label in range(1, background_count):
        x, y, component_width, component_height, area = stats[label]
        if area > max_area or x == 0 or y == 0:
            continue
        if x + component_width == width or y + component_height == height:
            continue
        region = labels[y : y + component_height, x : x + component_width] == label
        original = model_mask[y : y + component_height, x : x + component_width]
        if float(np.mean(original[region])) > 0.7:
            mask[y : y + component_height, x : x + component_width][region] = True
    return mask


def _external_shadow(salient: np.ndarray, shadow: np.ndarray) -> np.ndarray:
    """Reject broad cast shadows, not small dark markings on the subject."""
    points = np.column_stack(np.nonzero(salient))[:, ::-1].astype(np.int32)
    if len(points) < 3:
        return np.zeros_like(shadow)
    subject_hull = np.zeros(shadow.shape, dtype=np.uint8)
    cv2.fillConvexPoly(subject_hull, cv2.convexHull(points), 1)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        shadow.astype(np.uint8), connectivity=8
    )
    min_area = max(500, int(shadow.size * 0.003))
    external = np.zeros_like(shadow)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < min_area:
            continue
        component = labels == label
        outside = float(np.mean(subject_hull[component] == 0))
        if outside > 0.2:
            external[component] = True
    return external


def _remove_specks(mask: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    threshold = max(20, int(mask.size * 0.00002))
    cleaned = np.zeros_like(mask)
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= threshold:
            cleaned[labels == label] = True
    return cleaned


def extract_mask(
    rgb: np.ndarray,
    model_alpha: np.ndarray,
) -> tuple[np.ndarray, dict[str, object]]:
    """Fuse an external model mask with background evidence when reliable."""
    model_mask = model_alpha >= 128
    proposal = _background_proposal(rgb)
    report: dict[str, object] = {
        "model_coverage": round(float(np.mean(model_mask)), 5),
        "method": "model",
    }
    if proposal is None:
        mask = model_mask.copy()
        report["background"] = "complex_or_multicolour"
    else:
        salient, shadow, spread = proposal
        # A uniform border alone is not enough: there must also be a
        # substantial non-background subject, otherwise dark objects could be
        # mistaken for cast shadows.
        substantial = 0.015 <= float(np.mean(salient)) <= 0.75
        overlap = float(np.mean(model_mask[salient])) if salient.any() else 0.0
        report["border_chroma_spread"] = round(spread, 3)
        report["salient_coverage"] = round(float(np.mean(salient)), 5)
        report["salient_model_overlap"] = round(overlap, 4)
        if substantial and overlap >= 0.4:
            external_shadow = _external_shadow(salient, shadow & model_mask)
            mask = (model_mask & ~external_shadow) | salient
            mask = _fill_small_model_holes(mask, model_mask)
            report["method"] = "model_plus_uniform_background"
            report["external_shadow_pixels"] = int(np.count_nonzero(external_shadow))
        else:
            mask = model_mask.copy()
            report["background"] = "ambiguous_subject"
    mask = _remove_specks(mask)
    report["final_coverage"] = round(float(np.mean(mask)), 5)
    report["rescued_pixels"] = int(np.count_nonzero(mask & ~model_mask))
    report["removed_pixels"] = int(np.count_nonzero(model_mask & ~mask))
    report["touches_border"] = bool(
        np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1])
    )
    return mask, report


def _preview(rgb: np.ndarray, model_mask: np.ndarray, final_mask: np.ndarray) -> Image.Image:
    source = Image.fromarray(rgb, mode="RGB")
    overlay = rgb.astype(np.float32).copy()
    rescued = final_mask & ~model_mask
    removed = model_mask & ~final_mask
    overlay[rescued] = overlay[rescued] * 0.45 + np.asarray([0, 240, 70]) * 0.55
    overlay[removed] = overlay[removed] * 0.45 + np.asarray([255, 80, 0]) * 0.55
    border = cv2.morphologyEx(final_mask.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
    overlay[border] = [20, 230, 40]
    review = Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8), mode="RGB")
    combined = Image.new("RGB", (source.width * 2, source.height + 38), "#202020")
    combined.paste(source, (0, 38))
    combined.paste(review, (source.width, 38))
    draw = ImageDraw.Draw(combined)
    draw.text((12, 12), "ORIGINAL", fill="white")
    draw.text(
        (source.width + 12, 12),
        "GREEN: final edge / GREEN FILL: rescued / ORANGE: removed",
        fill="white",
    )
    return combined


def preprocess(image_path: Path, output_dir: Path) -> dict[str, object]:
    image = Image.open(image_path).convert("RGBA")
    rgba = np.asarray(image)
    rgb = rgba[:, :, :3].copy()
    original_alpha = rgba[:, :, 3]
    already_cut_out = float(np.mean(original_alpha < 16)) >= 0.01
    if already_cut_out:
        model_alpha = original_alpha
        mask = original_alpha >= 128
        report: dict[str, object] = {
            "method": "existing_alpha",
            "model_coverage": round(float(np.mean(mask)), 5),
            "final_coverage": round(float(np.mean(mask)), 5),
            "rescued_pixels": 0,
            "removed_pixels": 0,
            "touches_border": bool(
                np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1])
            ),
        }
        final_alpha = original_alpha.copy()
        final_alpha[final_alpha < 16] = 0
    else:
        import rembg

        session = rembg.new_session()
        cutout = rembg.remove(image, session=session)
        model_alpha = np.asarray(cutout.getchannel("A"))
        mask, report = extract_mask(rgb, model_alpha)
        final_alpha = cv2.GaussianBlur(mask.astype(np.uint8) * 255, (3, 3), 0.5)
        final_alpha[final_alpha < 16] = 0
    if not np.any(final_alpha >= 128):
        raise ValueError("No foreground survived; inspect the source image")

    output_dir.mkdir(parents=True, exist_ok=True)
    foreground = np.dstack((rgb, final_alpha))
    foreground[final_alpha == 0, :3] = 0
    Image.fromarray(foreground, mode="RGBA").save(output_dir / "foreground.png")
    Image.fromarray(final_alpha, mode="L").save(output_dir / "mask.png")
    _preview(rgb, model_alpha >= 128, final_alpha >= 128).save(output_dir / "review.png")
    report["source"] = str(image_path.resolve())
    report["size"] = [image.width, image.height]
    changed_fraction = (
        int(report["rescued_pixels"]) + int(report["removed_pixels"])
    ) / (image.width * image.height)
    report["changed_fraction"] = round(changed_fraction, 5)
    report["needs_review"] = bool(report["touches_border"] or changed_fraction > 0.025)
    (output_dir / "preprocess.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a shadow-aware RGBA subject for SF3D")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(preprocess(args.image, args.output_dir), indent=2), flush=True)


if __name__ == "__main__":
    main()
