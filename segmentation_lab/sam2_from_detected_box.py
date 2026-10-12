"""Run SAM 2 with a Florence-2-generated box, without hardcoded subject coordinates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw
from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detection-report", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"Output directory is not empty: {args.output_dir}")
    detection = json.loads(args.detection_report.read_text(encoding="utf-8"))
    if not detection.get("selected"):
        raise ValueError("Florence-2 did not select a box")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(detection["image"]).convert("RGB")
    rgb = np.asarray(image).copy()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_sam2("configs/sam2.1/sam2.1_hiera_s.yaml", str(args.checkpoint), device=device)
    predictor = SAM2ImagePredictor(model)
    records = []
    with torch.inference_mode():
        predictor.set_image(rgb)
        for name, box in [
            ("raw", detection["selected"]["box"]),
            ("padded", detection["padded_box"]),
        ]:
            masks, scores, _ = predictor.predict(box=np.asarray(box, dtype=np.float32), multimask_output=True)
            for index, (mask, score) in enumerate(zip(masks, scores)):
                mask = mask.astype(bool)
                prefix = f"{name}_{index}"
                Image.fromarray(mask.astype(np.uint8) * 255, "L").save(args.output_dir / f"{prefix}_mask.png")
                cutout = np.concatenate([rgb, (mask.astype(np.uint8) * 255)[..., None]], axis=2)
                Image.fromarray(cutout, "RGBA").save(args.output_dir / f"{prefix}_cutout.png")
                preview = rgb.astype(np.float32).copy()
                preview[~mask] *= 0.25
                preview[mask] = preview[mask] * 0.7 + np.array([0, 220, 60]) * 0.3
                Image.fromarray(preview.astype(np.uint8), "RGB").save(args.output_dir / f"{prefix}_overlay.png")
                records.append({"name": prefix, "box": box, "sam2_score": float(score), "area_fraction": float(mask.mean())})
    report = {"detection_report": str(args.detection_report.resolve()), "candidates": records}
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    tiles = []
    for item in records:
        pic = Image.open(args.output_dir / f"{item['name']}_overlay.png")
        pic.thumbnail((356, 308))
        tile = Image.new("RGB", (376, 340), "#222222")
        tile.paste(pic, ((376 - pic.width) // 2, 26))
        ImageDraw.Draw(tile).text((8, 5), f"{item['name']} score={item['sam2_score']:.3f}", fill="white")
        tiles.append(tile)
    sheet = Image.new("RGB", (3 * 376, 2 * 340), "#333333")
    for index, tile in enumerate(tiles):
        sheet.paste(tile, ((index % 3) * 376, (index // 3) * 340))
    sheet.save(args.output_dir / "contact_sheet.jpg", quality=92)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
