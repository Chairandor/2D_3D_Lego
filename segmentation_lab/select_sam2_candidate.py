"""Test a generic completeness-biased selector for boxed SAM 2 candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detection-report", type=Path, required=True)
    parser.add_argument("--sam-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--near-best-margin", type=float, default=0.01)
    parser.add_argument("--baseline-mask", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"Output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    detection = json.loads(args.detection_report.read_text(encoding="utf-8"))
    sam = json.loads(args.sam_report.read_text(encoding="utf-8"))
    raw = [item for item in sam["candidates"] if item["name"].startswith("raw_")]
    if not raw:
        raise ValueError("No raw-box SAM 2 candidates")
    best_score = max(item["sam2_score"] for item in raw)
    near_best = [item for item in raw if item["sam2_score"] >= best_score - args.near_best_margin]
    selected = max(near_best, key=lambda item: item["area_fraction"])
    source = np.asarray(Image.open(detection["image"]).convert("RGB"))
    mask_path = args.sam_report.parent / f"{selected['name']}_mask.png"
    mask = np.asarray(Image.open(mask_path).convert("L")) >= 128

    Image.fromarray(mask.astype(np.uint8) * 255, "L").save(args.output_dir / "mask.png")
    rgba = np.concatenate([source, (mask.astype(np.uint8) * 255)[..., None]], axis=2)
    Image.fromarray(rgba, "RGBA").save(args.output_dir / "foreground.png")
    preview = source.astype(np.float32).copy()
    preview[~mask] *= 0.25
    preview[mask] = preview[mask] * 0.7 + np.array([0, 220, 60]) * 0.3
    Image.fromarray(preview.astype(np.uint8), "RGB").save(args.output_dir / "overlay.png")

    report = {
        "image": detection["image"],
        "selection_rule": f"Raw box only; score >= best_score - {args.near_best_margin}; choose largest mask area",
        "selected": selected,
        "near_best_candidates": near_best,
        "warning": "Rule is a prototype; a single chicken image does not establish general reliability.",
    }
    if args.baseline_mask:
        baseline = np.asarray(Image.open(args.baseline_mask).convert("L")) >= 128
        if baseline.shape != mask.shape:
            raise ValueError("Baseline dimensions differ")
        union = baseline | mask
        report["baseline_comparison"] = {
            "baseline": str(args.baseline_mask.resolve()),
            "intersection_over_union": float((baseline & mask).sum() / max(1, union.sum())),
            "selected_only_pixels": int((mask & ~baseline).sum()),
            "baseline_only_pixels": int((baseline & ~mask).sum()),
        }
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
