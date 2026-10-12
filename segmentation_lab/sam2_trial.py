"""Isolated SAM 2.1 experiment. Never modifies the production pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw
from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor


def save_candidate(rgb: np.ndarray, mask: np.ndarray, path: Path) -> None:
    Image.fromarray((mask.astype(np.uint8) * 255), "L").save(path.with_name(path.stem + "_mask.png"))
    rgba = np.concatenate([rgb, (mask.astype(np.uint8) * 255)[..., None]], axis=2)
    Image.fromarray(rgba, "RGBA").save(path.with_name(path.stem + "_cutout.png"))
    marked = rgb.astype(np.float32).copy()
    marked[~mask] *= 0.25
    marked[mask] = marked[mask] * 0.7 + np.array([0, 220, 60]) * 0.3
    Image.fromarray(marked.astype(np.uint8), "RGB").save(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--automatic", action="store_true")
    parser.add_argument("--points-per-side", type=int, default=12)
    parser.add_argument("--points-per-batch", type=int, default=8)
    parser.add_argument("--pred-iou-thresh", type=float, default=0.75)
    parser.add_argument("--stability-score-thresh", type=float, default=0.9)
    parser.add_argument("--crop-n-layers", type=int, default=0)
    parser.add_argument("--box-nms-thresh", type=float, default=0.7)
    parser.add_argument("--skip-prompted", action="store_true")
    parser.add_argument("--max-auto-candidates", type=int, default=12)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"Output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rgb = np.asarray(Image.open(args.image).convert("RGB"))
    height, width = rgb.shape[:2]
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_sam2("configs/sam2.1/sam2.1_hiera_s.yaml", str(args.checkpoint), device=device)
    report = {
        "image": str(args.image.resolve()),
        "checkpoint": str(args.checkpoint.resolve()),
        "size": [width, height],
        "device": device,
        "note": "Prompted masks are diagnostic/manual, not a general automatic subject selector.",
        "automatic_parameters": {
            "points_per_side": args.points_per_side,
            "points_per_batch": args.points_per_batch,
            "pred_iou_thresh": args.pred_iou_thresh,
            "stability_score_thresh": args.stability_score_thresh,
            "crop_n_layers": args.crop_n_layers,
            "box_nms_thresh": args.box_nms_thresh,
        },
        "candidates": [],
    }

    # Approximate whole-subject rectangle, positive points on body/tail/feet,
    # negative points on the cast shadow. These are specific to this test image.
    box = np.array([285, 90, 1170, 1175], dtype=np.float32)
    points = np.array([[650, 530], [1055, 530], [810, 1040], [460, 940], [1210, 570]], dtype=np.float32)
    labels = np.array([1, 1, 1, 1, 0], dtype=np.int32)
    with torch.inference_mode():
        if not args.skip_prompted:
            predictor = SAM2ImagePredictor(model)
            predictor.set_image(rgb)
            for name, kwargs in [
                ("box_only", {"box": box}),
                ("box_points", {"box": box, "point_coords": points, "point_labels": labels}),
            ]:
                masks, scores, _ = predictor.predict(multimask_output=True, **kwargs)
                for index in np.argsort(scores)[::-1]:
                    mask = masks[index].astype(bool)
                    stem = f"{name}_{int(index)}"
                    save_candidate(rgb, mask, args.output_dir / f"{stem}.png")
                    report["candidates"].append({
                        "name": stem, "predicted_iou": float(scores[index]),
                        "area_fraction": float(mask.mean()), "kind": "manual_prompt",
                    })
        if args.automatic:
            generator = SAM2AutomaticMaskGenerator(
                model, points_per_side=args.points_per_side,
                points_per_batch=args.points_per_batch,
                pred_iou_thresh=args.pred_iou_thresh,
                stability_score_thresh=args.stability_score_thresh,
                crop_n_layers=args.crop_n_layers,
                box_nms_thresh=args.box_nms_thresh,
            )
            auto_masks = generator.generate(rgb)
            auto_masks.sort(key=lambda item: item["area"], reverse=True)
            report["automatic_candidate_count"] = len(auto_masks)
            for index, item in enumerate(auto_masks[:args.max_auto_candidates]):
                mask = item["segmentation"].astype(bool)
                stem = f"auto_area_rank_{index:02d}"
                save_candidate(rgb, mask, args.output_dir / f"{stem}.png")
                report["candidates"].append({
                    "name": stem, "predicted_iou": float(item["predicted_iou"]),
                    "area_fraction": float(mask.mean()), "kind": "automatic_candidate",
                })

    # Smaller thumbnails show the range of outputs without replacing originals.
    image_paths = list(args.output_dir.glob("*.png"))
    image_paths = [p for p in image_paths if not p.stem.endswith(("_mask", "_cutout"))]
    thumbs = []
    for path in image_paths:
        pic = Image.open(path).convert("RGB")
        pic.thumbnail((356, 308))
        tile = Image.new("RGB", (376, 340), "#222222")
        tile.paste(pic, ((376 - pic.width) // 2, 26))
        ImageDraw.Draw(tile).text((8, 5), path.stem, fill="white")
        thumbs.append(tile)
    columns = 4
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 376, rows * 340), "#333333")
    for index, tile in enumerate(thumbs):
        sheet.paste(tile, ((index % columns) * 376, (index // columns) * 340))
    sheet.save(args.output_dir / "contact_sheet.jpg", quality=92)
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
