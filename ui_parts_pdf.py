"""Export only the authoritative BOM as an illustrated, printable parts list."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import ui_lego_details

_lock = threading.Lock()


def export_pdf(model: Path, python: Path, ldraw: Path, root: Path, expected: str,
               thumbnails: dict, title: str) -> Path:
    if ui_lego_details.fingerprint(model) != expected:
        raise ValueError("模型已经更新，请重新打开成品展示后导出")
    bom = ui_lego_details.generate_bom(model, python, ldraw)
    if ui_lego_details.fingerprint(model) != expected:
        raise ValueError("模型已经更新，请重新打开成品展示后导出")
    title = str(title)[:150]
    key = hashlib.sha256(("parts-list/v1:" + expected + title).encode("utf-8")).hexdigest()[:24]
    directory = model.parent / ".ui-details"
    directory.mkdir(exist_ok=True)
    target = directory / f"parts-list-{key}.pdf"
    with _lock:
        if target.is_file() and target.stat().st_size > 0:
            return target
        if not isinstance(thumbnails, dict):
            raise ValueError("缺少零件缩略图")
        images = {}
        for part in bom["total"]:
            part_id = str(part["ldraw_part"]).removesuffix(".dat")
            ident = f'{part_id}:{part["colour_code"]}'
            image = thumbnails.get(ident, "")
            if not isinstance(image, str) or not image.startswith("data:image/png;base64,"):
                raise ValueError(f"零件 {part_id} 的缩略图尚未准备好，请重试导出")
            raw = base64.b64decode(image.split(",", 1)[1], validate=True)
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("零件缩略图格式无效")
            images[ident] = image
        fonts = [root / "instructions_workbench/assets/fonts/chinese.ttf",
                 Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")]
        font = next((p for p in fonts if p.is_file()), None)
        if font is None:
            raise ValueError("未找到中文字体，请先完成本机说明书环境配置")
        temporary = directory / f"parts-export-{uuid.uuid4().hex}.json"
        staged = directory / f"parts-export-{uuid.uuid4().hex}.pdf"
        try:
            temporary.write_text(json.dumps({"bom": bom, "thumbnails": images, "title": title}, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            result = subprocess.run([str(python), str(Path(__file__).resolve()), "--render",
                                     str(temporary), str(staged), str(font)],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=120, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError("零件清单导出失败：" + (result.stderr or result.stdout)[-1000:])
            if ui_lego_details.fingerprint(model) != expected:
                raise ValueError("导出期间模型已更新，请重新打开成品展示")
            staged.replace(target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
            staged.unlink(missing_ok=True)


def render_pdf(data: dict, target: Path, font: Path) -> dict:
    from PIL import Image
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from ui_instructions import COLOURS, part_name

    rows = data["bom"]["total"]
    if not rows or sum(int(p["quantity"]) for p in rows) != data["bom"]["brick_count"]:
        raise ValueError("零件数量与总清单不一致")
    if "PartsCN" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("PartsCN", str(font)))
    width, height = A4
    margin, per_page, row_height = 36, 12, 54
    pages = (len(rows) + per_page - 1) // per_page
    pdf = canvas.Canvas(str(target), pagesize=A4, pageCompression=1)
    pdf.setTitle(data["title"] + " · 零件清单")
    pdf.setAuthor("2D → 3D → LEGO")

    def text(x, y, value, size=10, colour="#253028", limit=None):
        value = str(value)
        if limit:
            while pdfmetrics.stringWidth(value, "PartsCN", size) > limit and size > 8:
                size -= .25
            while pdfmetrics.stringWidth(value, "PartsCN", size) > limit:
                value = value[:-2] + "…"
        pdf.setFillColor(HexColor(colour)); pdf.setFont("PartsCN", size)
        pdf.drawString(x, y, value)

    palette = {**COLOURS, "Bright_Light_Orange": "亮浅橙色", "Bright_Light_Yellow": "亮浅黄色",
               "Dark_Green": "深绿色", "Dark_Blue": "深蓝色", "Medium_Blue": "中蓝色"}
    for page in range(pages):
        text(margin, height - 41, "零件清单", 21)
        text(margin, height - 64, data["title"], 11, "#697465", width - 2 * margin)
        colour_count = len({r["colour_code"] for r in rows})
        text(margin, height - 85, f'{data["bom"]["brick_count"]} 件 · {len(rows)} 种零件与颜色组合 · {colour_count} 种颜色', 10, "#697465")
        pdf.setFillColor(HexColor("#edf1e8")); pdf.roundRect(margin, height - 120, width - 2 * margin, 24, 5, fill=1, stroke=0)
        for x, label in [(margin + 8, "零件预览"), (margin + 83, "零件 / 编号"), (margin + 300, "颜色"), (width - margin - 30, "数量")]:
            text(x, height - 112, label, 9, "#697465")
        for offset, part in enumerate(rows[page * per_page:(page + 1) * per_page]):
            top = height - 128 - offset * row_height
            part_id = str(part["ldraw_part"]).removesuffix(".dat")
            ident = f'{part_id}:{part["colour_code"]}'
            raw = base64.b64decode(data["thumbnails"][ident].split(",", 1)[1], validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or image.width > 1024 or image.height > 1024:
                    raise ValueError("缩略图格式或尺寸无效")
                image.load()
                reader = ImageReader(image.convert("RGBA"))
                pdf.setFillColor(HexColor("#e9ede7")); pdf.roundRect(margin, top - 45, 64, 45, 5, fill=1, stroke=0)
                pdf.drawImage(reader, margin + 2, top - 43, 60, 41, preserveAspectRatio=True, anchor="c", mask="auto")
            text(margin + 83, top - 16, part_name(str(part["part_key"])), 11, limit=204)
            text(margin + 83, top - 34, f'LDraw · {part["ldraw_part"]}', 9, "#798174", 204)
            text(margin + 300, top - 16, palette.get(part["colour_name"], part["colour_name"].replace("_", " ")), 10, limit=142)
            text(margin + 300, top - 34, f'颜色编号 · {part["colour_code"]}', 9, "#798174", 142)
            pdf.setFont("PartsCN", 16); pdf.setFillColor(HexColor("#253028"))
            pdf.drawRightString(width - margin - 8, top - 25, str(part["quantity"]))
            pdf.setStrokeColor(HexColor("#e7e9df")); pdf.line(margin, top - 50, width - margin, top - 50)
        text(margin, 22, "零件清单", 9, "#798174")
        pdf.setFont("PartsCN", 9); pdf.drawRightString(width - margin, 22, f"{page + 1} / {pages}")
        pdf.showPage()
    pdf.save()
    return {"pages": pages, "rows": len(rows), "quantity": data["bom"]["brick_count"]}


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--render":
        raise SystemExit("usage: ui_parts_pdf.py --render DATA PDF FONT")
    print(json.dumps(render_pdf(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")),
                               Path(sys.argv[3]), Path(sys.argv[4]))))
