"""Isolated subject-mask experiment; does not modify the production pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def run(
    image_path: Path,
    output_dir: Path,
    model_name: str,
    baseline_path: Path | None,
    sam_prompt: list[dict] | None = None,
) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Experiment output is not empty: {output_dir}")

    from rembg import new_session, remove

    source = Image.open(image_path).convert("RGBA")
    rgba = np.asarray(source)
    rgb = rgba[:, :, :3]
    session = new_session(model_name)
    remove_options = {"sam_prompt": sam_prompt} if sam_prompt is not None else {}
    result = remove(source, session=session, **remove_options).convert("RGBA")
    alpha = np.asarray(result.getchannel("A"))
    model_mask = alpha >= 128

    output_dir.mkdir(parents=True, exist_ok=True)
    result.save(output_dir / "foreground.png")
    Image.fromarray(alpha, mode="L").save(output_dir / "mask.png")

    report = {
        "source": str(image_path.resolve()),
        "model": model_name,
        "size": [source.width, source.height],
        "model_coverage": round(float(model_mask.mean()), 5),
        "touches_border": bool(
            model_mask[0].any()
            or model_mask[-1].any()
            or model_mask[:, 0].any()
            or model_mask[:, -1].any()
        ),
    }
    if sam_prompt is not None:
        report["sam_prompt"] = sam_prompt

    overlay = rgb.astype(np.float32).copy()
    overlay[~model_mask] *= 0.3
    overlay[model_mask] = overlay[model_mask] * 0.65 + np.array([0, 220, 80]) * 0.35
    panels = [Image.fromarray(rgb, mode="RGB"), Image.fromarray(overlay.astype(np.uint8), mode="RGB")]
    labels = ["SOURCE", f"{model_name.upper()} MASK: GREEN"]

    if baseline_path is not None:
        baseline_alpha = np.asarray(Image.open(baseline_path).convert("L"))
        if baseline_alpha.shape != alpha.shape:
            raise ValueError("Baseline mask dimensions do not match source")
        baseline_mask = baseline_alpha >= 128
        both = baseline_mask & model_mask
        model_only = model_mask & ~baseline_mask
        baseline_only = baseline_mask & ~model_mask
        difference = rgb.astype(np.float32).copy()
        difference[both] = difference[both] * 0.7 + np.array([0, 210, 70]) * 0.3
        difference[model_only] = difference[model_only] * 0.4 + np.array([0, 100, 255]) * 0.6
        difference[baseline_only] = difference[baseline_only] * 0.4 + np.array([255, 110, 0]) * 0.6
        panels.append(Image.fromarray(difference.astype(np.uint8), mode="RGB"))
        labels.append("GREEN: BOTH / BLUE: MODEL ONLY / ORANGE: OLD ONLY")
        union = baseline_mask | model_mask
        report.update(
            {
                "baseline": str(baseline_path.resolve()),
                "baseline_coverage": round(float(baseline_mask.mean()), 5),
                "intersection_over_union": round(float(both.sum() / max(1, union.sum())), 5),
                "model_only_pixels": int(model_only.sum()),
                "baseline_only_pixels": int(baseline_only.sum()),
            }
        )

    sheet = Image.new("RGB", (source.width * len(panels), source.height + 36), "#202020")
    draw = ImageDraw.Draw(sheet)
    for index, (panel, label) in enumerate(zip(panels, labels)):
        sheet.paste(panel, (index * source.width, 36))
        draw.text((index * source.width + 10, 10), label, fill="white")
    sheet.save(output_dir / "comparison.png")
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare a direct subject mask with an existing mask")
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default="birefnet-general-lite")
    parser.add_argument("--baseline-mask", type=Path)
    parser.add_argument(
        "--sam-point", nargs=3, action="append", metavar=("X", "Y", "LABEL"),
        type=int, help="SAM point: LABEL 1=subject, 0=background; may be repeated"
    )
    parser.add_argument(
        "--sam-box", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
        help="SAM rectangle around the intended subject"
    )
    args = parser.parse_args()
    if (args.sam_point or args.sam_box) and args.model != "sam":
        parser.error("--sam-point and --sam-box require --model sam")
    sam_prompt = None
    if args.model == "sam":
        sam_prompt = []
        if args.sam_box:
            sam_prompt.append({"type": "rectangle", "data": args.sam_box})
        for x, y, label in args.sam_point or []:
            if label not in (0, 1):
                parser.error("SAM point LABEL must be 0 or 1")
            sam_prompt.append({"type": "point", "data": [x, y], "label": label})
        if not sam_prompt:
            with Image.open(args.image) as source:
                sam_prompt.append({"type": "point", "data": [source.width // 2, source.height // 2], "label": 1})
    print(json.dumps(run(args.image, args.output_dir, args.model, args.baseline_mask, sam_prompt), indent=2))


if __name__ == "__main__":
    main()
