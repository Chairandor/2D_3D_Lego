"""Resolve the inputs for one explicit image-workbench action."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path

import proxe_integration as proxe
import ui_revisions
from staged_lego import atomic_json, read_preview


FOREGROUND = "input/foreground.png"
SHAPE = "hunyuan/mesh_shape_review_clean.glb"
SHEET = "unitex/mv_rgb.png"
COLOR = "unitex/textured_mesh.glb"
UPRIGHT = "oriented/mesh_upright.glb"
PLY = "oriented/lego_input.ply"
RECORD = "ui_stage_dependencies.json"
ACTIONS = {
    "foreground": "抠图",
    "geometry": "生成无色模型",
    "views": "生成六视图",
    "texture": "贴图",
    "lego": "生成 LEGO 预览",
}
VERSIONED_OUTPUTS = {"foreground": FOREGROUND, "geometry": SHAPE, "views": SHEET, "texture": COLOR}


def history(run: Path, root: Path) -> dict[str, dict[Path, dict]]:
    """Collect registered versions from the current result folder and its branches."""
    public = ui_revisions.root_run_dir(run, root)
    result = {relative: {} for relative in VERSIONED_OUTPUTS.values()}
    pending, visited = [public], set()
    while pending:
        owner = pending.pop().resolve()
        if owner in visited or not owner.is_relative_to(public):
            continue
        visited.add(owner)
        pending.extend(branch for branch in (owner / ui_revisions.REVISION_DIR / "branches").glob("*")
                       if branch.is_dir() and ui_revisions.branch_parent(branch, root) == owner)
        state = ui_revisions.load_state(owner)
        for relative, versions in result.items():
            def add(value, origin, source=owner):
                if not value:
                    return
                path, source = Path(value).resolve(), Path(source).resolve()
                if (path.is_relative_to(public) and path.is_file() and path.suffix == Path(relative).suffix
                        and source.is_relative_to(public) and source.is_dir()):
                    versions.setdefault(path, {"owner": source, "origin": origin})

            add(owner / relative, "生成")
            if relative == FOREGROUND:
                add(ui_revisions.branch_source_image(owner, root), "早期版本")
            elif relative == SHEET:
                settings = ui_revisions.branch_settings(owner, root)
                if settings:
                    add(settings["six_view"], "早期版本")
            for name, item in state.get("version_history", {}).get(relative, {}).items():
                add(name, item["origin"], item.get("owner") or owner)
            pair = state["pairs"].get(relative)
            if pair:
                add(pair.get("original"), "早期版本", (pair.get("six_view_lock") or {}).get("source_run_dir") or owner)
                add(pair.get("candidate"), pair.get("origin", "生成"),
                    (pair.get("six_view_lock") or {}).get("source_run_dir") or owner)
            if relative == COLOR:
                for edit in proxe.read_state(owner).get("history") or []:
                    if isinstance(edit, dict):
                        add(edit.get("output"), "3D 助手")
    _recover_png_history(result, public, visited)
    return result


@lru_cache(maxsize=256)
def _png_layout(path: Path, modified: int) -> tuple | None:
    from PIL import Image

    try:
        with Image.open(path) as image:
            return (image.size, image.mode) if image.format == "PNG" else None
    except (OSError, ValueError):
        return None


def _recover_png_history(result: dict, public: Path, owners: set[Path]) -> None:
    known = set().union(*(set(versions) for versions in result.values()))
    orphaned = [(path, owner) for owner in owners
                for entry in (owner / ui_revisions.REVISION_DIR / "assets").glob("*.png")
                if (path := entry.resolve()).is_relative_to(public) and path.is_file() and path not in known]
    if not orphaned:
        return
    layouts: dict[tuple, set[str]] = {}
    for relative in (FOREGROUND, SHEET):
        for path in result[relative]:
            if layout := _png_layout(path, path.stat().st_mtime_ns):
                layouts.setdefault(layout, set()).add(relative)
    for path, owner in orphaned:
        matching = layouts.get(_png_layout(path, path.stat().st_mtime_ns), set())
        if len(matching) == 1:
            relative = next(iter(matching))
            result[relative][path] = {"owner": owner, "origin": "早期版本"}


def metadata(run: Path) -> dict:
    try:
        value = json.loads((run / RECORD).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def identity(path: Path) -> dict:
    stamp = path.stat()
    return {"path": str(path.resolve()), "mtime": stamp.st_mtime_ns, "size": stamp.st_size}


@lru_cache(maxsize=32)
def _standard_transparent(path: Path, modified: int, size: int) -> bool:
    from pipeline_2d_to_lego import inspect_transparent_input

    try:
        return bool(inspect_transparent_input(path).get("accepted"))
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class Asset:
    file: Path
    owner: Path
    selected_at: int = 0
    version_owner: Path | None = None


class Console:
    def __init__(self, run: Path, image: Path | None, root: Path):
        self.run, self.image, self.root = run, image, root
        self.lineage = [run]
        parent = ui_revisions.branch_parent(run, root)
        while parent is not None and parent not in self.lineage:
            self.lineage.append(parent)
            parent = ui_revisions.branch_parent(parent, root)
        self.index = {owner: metadata(owner) for owner in self.lineage}
        self.cached: dict[str, Asset | None] = {}

    def asset(self, relative: str) -> Asset | None:
        for owner in self.lineage:
            version = ui_revisions.selected_version(owner, relative, self.root)
            record = ui_revisions.selected_pair(owner, relative, self.root)
            if record:
                chosen = record.get("choice") or "original"
                path = Path(str(record[chosen]))
                # A pending replacement keeps the already selected input.
                if path.is_file():
                    if version is None or path != Path(version["path"]):
                        return Asset(path, owner)
            if version:
                return Asset(Path(version["path"]), owner, version["selected_at"], Path(version["owner"]))
            if relative == FOREGROUND:
                if path := ui_revisions.branch_source_image(owner, self.root):
                    if not (owner / relative).is_file():
                        return Asset(path, owner)
            if relative == SHEET:
                settings = ui_revisions.branch_settings(owner, self.root)
                if settings and not (owner / relative).is_file():
                    return Asset(Path(settings["six_view"]), owner)
            path = (owner / relative).resolve()
            if path.is_relative_to(self.root.resolve()) and path.is_file():
                return Asset(path, owner)
        return None

    def _valid(self, relative: str, candidate: Asset, parents: dict[str, Path], selected_after: int = 0) -> bool:
        recorded = self.index[candidate.owner].get(relative, {})
        wanted = {name: identity(file) for name, file in parents.items()}
        actual = identity(candidate.file)
        if recorded.get("product") == actual:
            return recorded.get("inputs") == wanted
        # Old runs have no lineage records. A result older than a selected
        # input is an old result, except a transparent source copied as-is.
        return actual["mtime"] >= selected_after and all(actual["mtime"] >= item["mtime"] for item in wanted.values())

    def current(self, relative: str) -> Asset | None:
        if relative in self.cached:
            return self.cached[relative]
        candidate = self.asset(relative)
        required = {
            SHAPE: (FOREGROUND,), SHEET: (FOREGROUND, SHAPE),
            COLOR: (FOREGROUND, SHAPE, SHEET), UPRIGHT: (COLOR,), PLY: (COLOR,),
        }
        if candidate and candidate.selected_at:
            for name in required.get(relative, ()):
                upstream = self.asset(name)
                if upstream and max(upstream.selected_at, upstream.file.stat().st_mtime_ns) > candidate.selected_at:
                    candidate = None
                    break
            self.cached[relative] = candidate
            return candidate
        if relative == FOREGROUND:
            deps = {"source": self.image} if self.image and self.image.is_file() else {}
            if candidate and deps and not self._valid(relative, candidate, deps):
                candidate = None
            if candidate is None and self.image and self.image.is_file():
                stamp = self.image.stat()
                if _standard_transparent(self.image, stamp.st_mtime_ns, stamp.st_size):
                    candidate = Asset(self.image, self.run)
        else:
            dependencies = {name: self.current(name) for name in required[relative]}
            parents = {key: value.file for key, value in dependencies.items() if value is not None}
            sheet = dependencies.get(SHEET)
            if relative == COLOR and sheet and sheet.version_owner:
                try:
                    parents = self._historical_texture_inputs(sheet)
                    parents.pop("cache_run")
                except (OSError, ValueError):
                    parents = {}
            selected_after = max((asset.selected_at for asset in dependencies.values() if asset), default=0)
            manual = self._manually_selected(relative, candidate)
            if candidate and not (
                (len(dependencies) == len(parents) and self._valid(relative, candidate, parents, selected_after))
                or (manual and self._manual_is_current(relative, candidate, parents))
            ):
                candidate = None
        self.cached[relative] = candidate
        return candidate

    def _manually_selected(self, relative: str, candidate: Asset | None) -> bool:
        if candidate is None:
            return False
        rec = ui_revisions.selected_pair(candidate.owner, relative, self.root)
        return bool(rec and rec.get("choice") == "candidate" and rec.get("origin") != "generated")

    def _manual_is_current(self, relative: str, asset: Asset, parents: dict[str, Path]) -> bool:
        if relative == SHEET:
            return False
        if relative in {UPRIGHT, PLY, COLOR} and not parents:
            # An explicit GLB / PLY replacement in the active branch can be
            # used as the starting model even when an old six-view is stale.
            return asset.owner == self.run
        return self._valid(relative, asset, parents)

    def _cache(self, reference: Path, shape: Path) -> Path:
        parents = {FOREGROUND: identity(reference), SHAPE: identity(shape)}
        for owner in self.lineage:
            record = self.index[owner].get(SHEET, {})
            if record:
                if record.get("inputs") == parents and record.get("cache_run"):
                    return Path(record["cache_run"])
                continue
            sheet = owner / SHEET
            if sheet.is_file() and self._valid(SHEET, Asset(sheet, owner), {FOREGROUND: reference, SHAPE: shape}):
                return owner
        raise ValueError("当前六视图缺少可用的原始缓存，请先生成六视图")

    def _historical_texture_inputs(self, sheet: Asset) -> dict[str, Path]:
        source = Console(sheet.version_owner, self.image, self.root)
        for owner in source.lineage:
            recorded = source.index[owner].get(SHEET)
            if recorded and recorded.get("cache_run"):
                inputs = recorded.get("inputs", {})
                ref = Path(inputs.get(FOREGROUND, {}).get("path", "")).resolve()
                shape = Path(inputs.get(SHAPE, {}).get("path", "")).resolve()
                cache = Path(recorded["cache_run"]).resolve()
                if (all(path.is_relative_to(self.root.resolve()) and path.is_file() for path in (ref, shape))
                        and cache.is_relative_to(owner) and cache.is_dir()
                        and inputs == {FOREGROUND: identity(ref), SHAPE: identity(shape)}):
                    return {FOREGROUND: ref, SHAPE: shape, SHEET: sheet.file, "cache_run": cache}
                break
            settings = ui_revisions.branch_settings(owner, self.root)
            if settings:
                ref, shape = Path(settings["source_image"]), Path(settings["from_shape"])
                return {FOREGROUND: ref, SHAPE: shape, SHEET: sheet.file, "cache_run": source._cache(ref, shape)}
            if (owner / SHEET).is_file():
                ref = ui_revisions.available_artifact(owner, FOREGROUND, self.root)
                shape = ui_revisions.available_artifact(owner, SHAPE, self.root)
                return {FOREGROUND: ref, SHAPE: shape, SHEET: sheet.file, "cache_run": source._cache(ref, shape)}
        raise ValueError("这版六视图缺少匹配的原始缓存，请重新生成六视图")

    def _lego_model(self) -> dict[str, Path]:
        textured = self.current(COLOR)
        upright, ply = self.current(UPRIGHT), self.current(PLY)
        if textured and textured.selected_at:
            return {"ply": ply.file} if ply else {"glb": textured.file}
        edit = proxe.read_state(self.run)
        if edited := proxe.selected_lego_input(self.run):
            accepted = edit.get("selected_model")
            history = edit.get("history", [])
            sources = {str(asset.file) for asset in (textured, upright) if asset}
            legacy_original = proxe.original_model(self.run)
            if legacy_original and not (self.run / COLOR).is_file():
                sources.add(str(legacy_original))
            if isinstance(history, list):
                matching = {str(entry["output"]): str(entry["source"]) for entry in history
                            if isinstance(entry, dict) and entry.get("output") and entry.get("source")}
                visited = set()
                source = str(accepted)
                while source in matching and source not in visited:
                    visited.add(source)
                    source = matching[source]
                if source in sources and all(edited.stat().st_mtime_ns >= Path(p).stat().st_mtime_ns
                                             for p in sources):
                    return {"ply": edited}
        if self._manually_selected(UPRIGHT, upright):
            if ply is not None and ply.file.stat().st_mtime_ns >= upright.file.stat().st_mtime_ns:
                return {"ply": ply.file}
            return {"glb": upright.file}
        if ply is not None:
            return {"ply": ply.file}
        if textured is not None:
            return {"glb": textured.file}
        # Directly uploaded GLBs intentionally skip the image stages.
        try:
            imported = json.loads((self.run / "pipeline_state.json").read_text(encoding="utf-8")).get("pipeline")
        except (OSError, ValueError, AttributeError):
            imported = None
        if imported in {"direct-3d-import", "existing-run-import"}:
            if source := proxe.current_model(self.run):
                return {"glb": source}
        raise ValueError("先生成或采用当前版本的彩色模型")

    def inputs(self, action: str) -> dict[str, Path]:
        if action == "foreground":
            if not self.image or not self.image.is_file():
                raise ValueError("先上传或填写原始图片")
            return {"source": self.image}
        if action == "lego":
            return self._lego_model()
        if action == "texture":
            sheet = self.current(SHEET)
            if sheet and sheet.version_owner:
                return self._historical_texture_inputs(sheet)
        required = {
            "geometry": ((FOREGROUND, "先抠图或使用标准透明底图"),),
            "views": ((FOREGROUND, "缺少透明底图"), (SHAPE, "先生成或采用无色模型")),
            "texture": ((FOREGROUND, "缺少透明底图"), (SHAPE, "先生成或采用无色模型"),
                        (SHEET, "先为当前无色模型生成或采用六视图")),
        }
        result = {}
        for relative, note in required[action]:
            asset = self.current(relative)
            if asset is None:
                raise ValueError(note)
            result[relative] = asset.file
        if action == "texture":
            result["cache_run"] = self._cache(result[FOREGROUND], result[SHAPE])
        return result

    def buttons(self) -> dict[str, str]:
        missing = {}
        for action in ACTIONS:
            try:
                self.inputs(action)
            except (OSError, ValueError) as exc:
                missing[action] = str(exc)
        return missing

    def outdated(self, relative: str, group: dict) -> bool:
        if relative in {FOREGROUND, SHAPE, SHEET, COLOR, UPRIGHT, PLY}:
            return self.current(relative) is None
        if relative.startswith("lego-") and relative.endswith("/model.mpd"):
            folder = Path(relative).parent
            for owner in self.lineage:
                saved = self.index[owner].get(relative)
                if saved is None:
                    if (owner / relative).is_file() or read_preview(owner / folder):
                        break
                    continue
                marker = read_preview(owner / folder)
                if marker and marker.get("token") == saved.get("product"):
                    try:
                        return saved["inputs"] != {key: identity(value) for key, value in self._lego_model().items()}
                    except (OSError, ValueError):
                        return True
                break
        return bool(group.get("stale"))


def save_outputs(run: Path, root: Path, outputs: dict, inputs: dict[str, Path]) -> None:
    saved = metadata(run)
    stamps = {key: identity(value) for key, value in inputs.items() if key != "cache_run"}
    for relative, settings in outputs.items():
        saved[relative] = {"product": identity(run / relative), "inputs": stamps, **settings}
    atomic_json(run / RECORD, saved)
    state = ui_revisions.load_state(run)
    if any(relative in state["pairs"] or relative in state.get("selected_versions", {}) for relative in outputs):
        def remove_replaced(payload):
            for relative in outputs:
                ui_revisions.remember_pair(payload, relative, payload["pairs"].pop(relative, None))
                payload.get("selected_versions", {}).pop(relative, None)
        ui_revisions.update_state(run, remove_replaced)
