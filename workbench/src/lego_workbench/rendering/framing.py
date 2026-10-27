"""Reused native LeoCAD framing/cache adapter; no stability calls."""
from __future__ import annotations

import argparse

from dataclasses import replace

from datetime import datetime

import hashlib

import html

import itertools

import json

import math

from pathlib import Path

import re

import shutil

import struct

import subprocess

import sys

FIX_VERSION = "060-render-review-1"

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def sanitize(text):
    return "\n".join(s for s in text.splitlines() if s.split()[:2] != ["0", "ROTSTEP"]) + "\n"

def sections(text, fallback):
    result, current = {}, None
    for line in text.splitlines():
        if line.upper().startswith("0 FILE "):
            current = line[7:].strip().casefold()
            result[current] = []
        elif line.strip().upper() == "0 NOFILE":
            current = None
        elif current is not None:
            result[current].append(line)
    return result or {fallback.casefold(): text.splitlines()}

def corners(box):
    return itertools.product(*[(box[0][i], box[1][i]) for i in range(3)])

def merge(points):
    points = list(points)
    if not points:
        raise ValueError("没有可用于取景的几何坐标")
    return tuple(min(p[i] for p in points) for i in range(3)), tuple(max(p[i] for p in points) for i in range(3))

class Geometry:
    """Read native LDraw geometry, including nested transforms, without import/LP."""
    def __init__(self, library):
        self.library = Path(library)
        self.cache = {}
        self.model_cache = {}

    def external(self, name):
        name = name.replace("\\", "/")
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"非法零件引用: {name}")
        for root in [self.library / "parts", self.library / "p", self.library / "models", self.library]:
            candidate = root / name
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(f"零件库中缺少几何文件: {name}")

    def lines_box(self, lines, local, stack):
        points = []
        for line in lines:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "1" and len(fields) >= 15:
                fields = line.split(maxsplit=14)
                ref = fields[14].strip().casefold()
                if ref in local:
                    if ref in stack:
                        raise ValueError(f"循环子模型引用: {ref}")
                    box = self.lines_box(local[ref], local, stack | {ref})
                else:
                    path = self.external(ref)
                    if str(path) in stack:
                        raise ValueError(f"循环零件引用: {ref}")
                    if path not in self.cache:
                        self.cache[path] = self.lines_box(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), {}, stack | {str(path)})
                    box = self.cache[path]
                xyz = list(map(float, fields[2:5]))
                matrix = list(map(float, fields[5:14]))
                points.extend(tuple(xyz[i] + sum(matrix[3*i+j]*p[j] for j in range(3)) for i in range(3)) for p in corners(box))
            elif fields[0] in {"2", "3", "4", "5"}:
                # Conditional-line control points are conservative bounds too.
                values = list(map(float, fields[2:]))
                points.extend(tuple(values[i:i+3]) for i in range(0, len(values)-2, 3))
        return merge(points)

    def model_box(self, path):
        text = sanitize(Path(path).read_text(encoding="utf-8-sig"))
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in self.model_cache:
            local = sections(text, Path(path).name)
            first = next(iter(local))
            self.model_cache[key] = self.lines_box(local[first], local, {first})
        return self.model_cache[key]

def camera(box, latitude, longitude, width, height):
    """Conservative sphere fit in LDraw coordinates, preserving LeoCAD yaw."""
    center = tuple((box[0][i] + box[1][i])/2 for i in range(3))
    radius = max(1.0, math.dist(box[0], box[1])/2)
    vfov = math.radians(30)
    hfov = 2 * math.atan(math.tan(vfov/2) * width/height)
    # Sphere tangency + 25% distance margin; parts and studs share these bounds.
    distance = 1.25 * radius / math.sin(min(vfov, hfov)/2)
    lat, lon = math.radians(latitude), math.radians(longitude)
    direction = (math.sin(lon)*math.cos(lat), -math.sin(lat), -math.cos(lon)*math.cos(lat))
    up = (-math.sin(lon)*math.sin(lat), -math.cos(lat), math.cos(lon)*math.sin(lat))
    position = tuple(center[i] + distance*direction[i] for i in range(3))
    return (*position, *center, *up), (1.0, max(100000.0, distance + 4*radius))

def png_ok(path, width, height):
    if not path.is_file():
        return False
    data = path.read_bytes()
    return len(data) > 100 and data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24]) == (width, height)

class FramedRunner:
    def __init__(self, library, cache, executor=None):
        self.geometry = Geometry(library)
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.executor = executor or subprocess.run
        self.completed = 0
        self.cached = 0

    def __call__(self, original, timeout):
        cmd = list(original)
        model = Path(cmd[1])
        first, last = int(cmd[cmd.index("-f")+1]), int(cmd[cmd.index("-t")+1])
        output = Path(cmd[cmd.index("-i")+1])
        width, height = int(cmd[cmd.index("-w")+1]), int(cmd[cmd.index("-h")+1])
        angle_at = cmd.index("--camera-angles")
        lat, lon = float(cmd[angle_at+1]), float(cmd[angle_at+2])
        box = self.geometry.model_box(model)
        values, planes = camera(box, lat, lon, width, height)
        del cmd[angle_at:angle_at+3]
        cmd += ["--camera-position-ldraw", *[f"{x:.9g}" for x in values], "--fov", "30", "--zplanes", *[f"{x:.9g}" for x in planes]]
        # Explicit camera avoids saved preferences and automatic origin framing.
        model_hash = hashlib.sha256(sanitize(model.read_text(encoding="utf-8-sig")).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([FIX_VERSION, model_hash, lat, lon, width, height, str(cmd[0]), str(self.geometry.library)], sort_keys=True).encode()).hexdigest()
        cachedir = self.cache / key
        cachedir.mkdir(exist_ok=True)
        outputs = {i: output if first == last else output.with_name(f"{output.stem}{i:02d}{output.suffix}") for i in range(first, last+1)}
        missing = [i for i in outputs if not png_ok(cachedir/f"{i}.png", width, height)]
        stderr = ""
        if missing:
            # Group consecutive uncached frames, reusing the two-page preview.
            ranges = []
            for i in missing:
                if ranges and i == ranges[-1][1]+1:
                    ranges[-1] = (ranges[-1][0], i)
                else:
                    ranges.append((i, i))
            for a, b in ranges:
                actual = list(cmd)
                dest = cachedir / "frame.png"
                actual[actual.index("-i")+1] = str(dest)
                actual[actual.index("-f")+1] = str(a)
                actual[actual.index("-t")+1] = str(b)
                print(f"补渲染当前视角第 {a}-{b} 步", flush=True)
                process = self.executor(actual, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
                stderr += process.stderr or ""
                for i in range(a, b+1):
                    generated = dest if a == b else dest.with_name(f"frame{i:02d}.png")
                    if not png_ok(generated, width, height):
                        raise RuntimeError(f"LeoCAD 未生成完整 PNG（局部步骤 {i}）: {stderr[-1200:]}")
                    generated.replace(cachedir/f"{i}.png")
        for i, dest in outputs.items():
            shutil.copyfile(cachedir/f"{i}.png", dest)
            self.completed += 1
            self.cached += int(i not in missing)
        print(f"本次输出步骤图 {self.completed}，复用新取景缓存 {self.cached}", flush=True)
        return stderr

def restore_plan(payload):
    from legolization.instructions import BillOfMaterials, BomEntry, BuildStep, InstructionPlan, RotStep, Subassembly
    warning_flags = {int(m.group(1)) for s in payload["warnings"] if (m := re.match(r"^step (\d+): insertion-fragile", s))}
    repaired = []
    steps = []
    for item in payload["steps"]:
        row = dict(item)
        if row["index"] in warning_flags and not row["insertion_fragile"]:
            repaired.append(row["index"])
            row["insertion_fragile"] = True
        row["brick_ids"] = tuple(row["brick_ids"])
        row["rotstep"] = RotStep(**row["rotstep"]) if row["rotstep"] else None
        steps.append(BuildStep(**row))
    bom = payload["bom"]
    plan = InstructionPlan(steps=tuple(steps), warnings=tuple(payload["warnings"]),
        bom=BillOfMaterials(total=tuple(BomEntry(**x) for x in bom["total"]), per_step=tuple(tuple(BomEntry(**x) for x in row) for row in bom["per_step"])),
        subassemblies=tuple(Subassembly(**dict(x, brick_ids=tuple(x["brick_ids"]))) for x in payload["subassemblies"]))
    return plan, repaired

def step_views(plan):
    world = plan.main_steps()
    view, result = 0.0, {}
    for ordinal, step in enumerate(world, 1):
        if step.rotstep:
            r = step.rotstep
            view = (view+r.yaw)%360 if r.mode == "REL" else r.yaw%360 if r.mode == "ABS" else 0.0
        result[step.index] = (None, ordinal, (45-view)%360)
    for sub in plan.subassemblies:
        for ordinal, step in enumerate(plan.sub_steps(sub.name), 1):
            result[step.index] = (sub.name, ordinal, 45.0)
    return result
