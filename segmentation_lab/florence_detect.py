"""Automatic Florence-2 subject-box experiment; no image-specific coordinates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from transformers import AutoModelForCausalLM, AutoProcessor
from transformers import dynamic_module_utils


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error(f"Output directory is not empty: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    image = Image.open(args.image).convert("RGB")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    # Transformers 4.42's static custom-code import scan mistakes Florence's
    # conditional flash_attn import for a hard dependency. The code itself
    # checks availability before importing it, so exclude only that optional
    # package from the static scan on native Windows.
    original_get_imports = dynamic_module_utils.get_imports

    def get_required_imports(filename):
        return [name for name in original_get_imports(filename) if name != "flash_attn"]

    dynamic_module_utils.get_imports = get_required_imports
    processor = AutoProcessor.from_pretrained(args.model_dir, trust_remote_code=True, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        args.model_dir, torch_dtype=dtype, trust_remote_code=True, local_files_only=True
    ).to(device).eval()

    def infer(task: str, text: str = "") -> dict | str:
        inputs = processor(text=task + text, images=image, return_tensors="pt")
        inputs = inputs.to(device, dtype)
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=256, num_beams=3, do_sample=False)
        generated = processor.batch_decode(output, skip_special_tokens=False)[0]
        return processor.post_process_generation(generated, task=task, image_size=image.size)

    print("Generating caption...", flush=True)
    caption_result = infer("<DETAILED_CAPTION>")
    caption = caption_result.get("<DETAILED_CAPTION>", "") if isinstance(caption_result, dict) else str(caption_result)
    print(f"Caption: {caption}", flush=True)
    print("Detecting objects...", flush=True)
    detection = infer("<OD>")
    print(f"Detection: {detection}", flush=True)
    grounding = None
    if caption.strip():
        print("Grounding caption...", flush=True)
        grounding = infer("<CAPTION_TO_PHRASE_GROUNDING>", caption)
        print(f"Grounding: {grounding}", flush=True)

    candidates = []
    for source, result, key in [
        ("object_detection", detection, "<OD>"),
        ("caption_grounding", grounding, "<CAPTION_TO_PHRASE_GROUNDING>"),
    ]:
        data = result.get(key, {}) if isinstance(result, dict) else {}
        for box, label in zip(data.get("bboxes", []), data.get("labels", [])):
            x0, y0, x1, y1 = [float(v) for v in box]
            x0, x1 = sorted((max(0, x0), min(image.width, x1)))
            y0, y1 = sorted((max(0, y0), min(image.height, y1)))
            area = (x1 - x0) * (y1 - y0) / (image.width * image.height)
            if x1 > x0 and y1 > y0:
                candidates.append({"source": source, "label": str(label), "box": [x0, y0, x1, y1], "area_fraction": area})

    # Explicit, generic baseline: select the largest non-image-spanning detection.
    # It is a heuristic to test, not a claim that object identity is solved.
    eligible = [c for c in candidates if 0.01 <= c["area_fraction"] <= 0.85]
    selected = max(eligible, key=lambda c: c["area_fraction"]) if eligible else None
    report = {
        "image": str(args.image.resolve()), "model": str(args.model_dir.resolve()),
        "device": device, "caption": caption, "object_detection": detection,
        "caption_grounding": grounding, "candidates": candidates,
        "selection_rule": "largest box with area fraction 0.01..0.85; no image-specific coordinates",
        "selected": selected,
    }
    if selected:
        x0, y0, x1, y1 = selected["box"]
        padding_x, padding_y = (x1 - x0) * 0.06, (y1 - y0) * 0.06
        report["padded_box"] = [
            max(0, x0 - padding_x), max(0, y0 - padding_y),
            min(image.width, x1 + padding_x), min(image.height, y1 + padding_y),
        ]
    preview = image.copy()
    draw = ImageDraw.Draw(preview)
    for candidate in candidates:
        coords = candidate["box"]
        draw.rectangle(coords, outline="#ff5252", width=3)
        draw.text((coords[0], max(0, coords[1] - 16)), candidate["label"][:45], fill="#ff5252")
    if selected:
        draw.rectangle(selected["box"], outline="#00ff5c", width=7)
        draw.rectangle(report["padded_box"], outline="#00ddff", width=4)
    preview.save(args.output_dir / "boxes.png")
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidate_count": len(candidates), "selected": selected, "padded_box": report.get("padded_box")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
