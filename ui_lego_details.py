"""LEGO detail metadata and a solver-free adapter to Legolization's native BOM."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

_bom_lock = threading.Lock()


def model_path(value: str, root: Path) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.suffix.lower() not in {".mpd", ".ldr"}:
        raise ValueError("找不到项目内的 LEGO 模型")
    return path


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cached_bom(path: Path) -> dict | None:
    digest = fingerprint(path)
    cache = path.parent / ".ui-details" / f"{path.stem}-{digest}.bom.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("source_sha256") == digest and isinstance(data.get("total"), list):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    # A bundle's own BOM describes this exact canonical model, rather than a
    # nearby alternative or the original card's potentially different build.
    if path.parent.name == "model":
        native = path.parent.parent / "bom" / "bom.json"
        try:
            if native.stat().st_mtime_ns >= path.stat().st_mtime_ns:
                data = json.loads(native.read_text(encoding="utf-8"))
                if isinstance(data.get("total"), list):
                    return data
        except (OSError, ValueError, AttributeError):
            pass
    return None


def describe(path: Path, *, ldraw_available: bool) -> dict:
    comparison_url = None
    parent = path.parent.name
    if parent.startswith("lego-") and parent[5:].isdigit():
        comparison_url = "/compare?" + urllib.parse.urlencode({
            "run_dir": str(path.parent.parent), "studs": int(parent[5:]), "embedded": "1",
        })
    return {
        "path": str(path), "name": path.name, "folder": parent,
        "format": path.suffix.lower()[1:], "mtime_ns": path.stat().st_mtime_ns,
        "source_sha256": fingerprint(path),
        "url": "/file?" + urllib.parse.urlencode({"path": str(path)}),
        "ldraw_available": ldraw_available, "bom": cached_bom(path),
        "comparison_url": comparison_url,
    }


def generate_bom(path: Path, python: Path, ldraw: Path) -> dict:
    if not python.is_file():
        raise ValueError("未找到 Legolization 环境，请先完成本机配置")
    with _bom_lock:
        existing = cached_bom(path)
        if existing is not None:
            return existing
        digest = fingerprint(path)
        env = os.environ.copy()
        if ldraw.is_dir():
            env["LDRAWDIR"] = str(ldraw)
        result = subprocess.run(
            [str(python), str(Path(__file__).resolve()), "--native-bom", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
        if result.returncode:
            raise RuntimeError("零件统计生成失败：" + (result.stderr.strip() or result.stdout.strip())[-1600:])
        data = json.loads(result.stdout)
        if not isinstance(data.get("total"), list):
            raise RuntimeError("原生 BOM 未返回有效零件列表")
        if fingerprint(path) != digest:
            raise RuntimeError("统计期间模型已更新，请重新生成统计")
        data["source_sha256"] = digest
        directory = path.parent / ".ui-details"
        directory.mkdir(exist_ok=True)
        target = directory / f"{path.stem}-{digest}.bom.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return data


def native_bom(path: Path) -> dict:
    from legolization.color import default_palette
    from legolization.instructions.bom import bill_of_materials
    from legolization.ldraw_in import layout_from_ldraw

    try:
        default_palette()
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("ldraw.library"):
            raise
        # Some installations have the official parts library but no generated
        # Python colour module. Adapt its native catalog in this worker only.
        import types
        from ldraw.parts import Parts

        library = Path(os.environ.get("LDRAWDIR", str(Path(__file__).parent / "LDrawLibrary" / "ldraw")))
        parts = Parts(library / "parts.lst")
        colours = types.ModuleType("ldraw.library.colours")
        vars(colours).update(parts.colours_by_name)
        sys.modules["ldraw.library.colours"] = colours

    layout = layout_from_ldraw(path, ground=True)
    data = json.loads(bill_of_materials(layout).to_json(model_name=path.stem))
    palette = default_palette()
    for entry in data["total"]:
        entry["colour_hex"] = "#" + "".join(f"{channel:02x}" for channel in palette.rgb_of(entry["colour_code"]))
    return data


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--native-bom":
        raise SystemExit("usage: ui_lego_details.py --native-bom MODEL")
    print(json.dumps(native_bom(Path(sys.argv[2])), ensure_ascii=False))
