"""Read local LDraw triangle geometry for AR; never download or alter assets.

Format reference: https://www.ldraw.org/article/218.html
Parts and their licences remain in the user's existing LDraw library.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import os
from pathlib import Path

import numpy as np
import trimesh


def find_library(explicit: str = '') -> Path:
    """Locate a complete local parts library, or explain how to select one."""
    local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
    candidates = [Path(explicit)] if explicit else []
    if os.environ.get('LDRAWDIR'):
        candidates.append(Path(os.environ['LDRAWDIR']))
    candidates += [local / 'legolization/ldraw', Path.home() / 'ldraw']
    candidates += list((local / 'pyldraw3').glob('**/complete/ldraw'))
    for root in candidates:
        if (root / 'parts').is_dir() and (root / 'p').is_dir() and (root / 'LDConfig.ldr').is_file():
            return root.resolve()
    raise FileNotFoundError('未找到 LDraw 零件库。请用 --ldraw-dir 指向含 parts、p、LDConfig.ldr 的目录。')


def load_ldraw(path: str, library: str = '', studio_colors: str = '') -> list[trimesh.Trimesh]:
    """Expand LDR/MPD references and real part surfaces into colored Z-up meshes.

    Supports inherited/direct colors, affine transforms, triangles and quads.
    Edge/conditional lines and texture/material effects are not rendered.
    Missing references fail explicitly rather than showing an incomplete model.
    """
    root = find_library(library)
    source = Path(path).resolve(strict=True)
    colors: dict[str, list[int]] = {}

    def read_colors(lines: list[str]) -> None:
        for line in lines:
            fields = line.split()
            if len(fields) > 8 and fields[:2] == ['0', '!COLOUR']:
                code = fields[fields.index('CODE') + 1]
                rgb = fields[fields.index('VALUE') + 1].lstrip('#')
                alpha = int(fields[fields.index('ALPHA') + 1]) if 'ALPHA' in fields else 255
                colors[code] = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)] + [alpha]

    read_colors((root / 'LDConfig.ldr').read_text(encoding='utf-8-sig').splitlines())
    if studio_colors:
        with Path(studio_colors).open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream, delimiter='\t'):
                code, rgb = row['LDraw Color Code'], row['RGB value'].lstrip('#')
                if code and code not in colors and len(rgb) == 6:
                    colors[code] = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)] + [round(float(row['Alpha']) * 255)]
        print(f'Studio color definitions: {studio_colors}', flush=True)
    files: dict[Path, dict[str, list[str]]] = {}
    cache: dict[tuple[Path, str, str], dict[str, np.ndarray]] = {}
    active: set[tuple[Path, str, str]] = set()

    def sections(file: Path) -> dict[str, list[str]]:
        if file not in files:
            lines = file.read_text(encoding='utf-8-sig').splitlines()
            read_colors(lines)
            result: dict[str, list[str]] = {}
            current = None
            for line in lines:
                if line.upper().startswith('0 FILE '):
                    current = line[7:].strip().replace('\\', '/').lower()
                    result[current] = []
                elif line.upper().strip() == '0 NOFILE':
                    current = None
                elif current is not None:
                    result[current].append(line)
            files[file] = result or {file.name.lower(): lines}
        return files[file]

    def expand(file: Path, name: str, inherited: str) -> dict[str, np.ndarray]:
        token = (file, name, inherited)
        if token in active:
            raise ValueError(f'LDraw 循环引用：{file} / {name}')
        if token in cache:
            return cache[token]
        active.add(token)
        groups: dict[str, list[np.ndarray]] = defaultdict(list)
        for line in sections(file)[name]:
            fields = line.strip().split(maxsplit=14)
            if not fields or fields[0] not in ('1', '3', '4'):
                continue
            color = inherited if fields[1] == '16' else fields[1]
            if fields[0] == '1':
                if len(fields) != 15:
                    raise ValueError(f'无效 LDraw 引用：{line}')
                offset = np.asarray(fields[2:5], dtype=float)
                matrix = np.asarray(fields[5:14], dtype=float).reshape(3, 3)
                ref = fields[14].replace('\\', '/').lower()
                if ref in sections(file):
                    child, section = file, ref
                else:
                    candidates = [file.parent / ref, root / 'parts' / ref, root / 'p' / ref, root / ref]
                    child = next((p.resolve() for p in candidates if p.is_file()), None)
                    if child is None:
                        raise FileNotFoundError(f'缺少 LDraw 零件 {ref}（引用自 {file}）；请指定完整零件库。')
                    section = next(iter(sections(child)))
                for child_color, triangles in expand(child, section, color).items():
                    groups[child_color].append(triangles @ matrix.T + offset)
            else:
                vertices = np.asarray(fields[2:], dtype=float).reshape(-1, 3)
                triangles = vertices[None, :3, :]
                if fields[0] == '4':
                    triangles = np.stack((vertices[[0, 1, 2]], vertices[[0, 2, 3]]))
                groups[color].append(triangles)
        active.remove(token)
        cache[token] = {color: np.concatenate(parts) for color, parts in groups.items()}
        return cache[token]

    geometry = expand(source, next(iter(sections(source))), '7')
    meshes = []
    # LDraw uses -Y up. Rotate +90 degrees about X into +Z up (right handed).
    rotation = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]])
    for color, triangles in geometry.items():
        if color.lower().startswith('0x2'):
            rgb = color[3:]
            rgba = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)] + [255]
        else:
            if color not in colors:
                raise ValueError(f'零件库没有颜色 {color}，请使用匹配的 LDConfig.ldr。')
            rgba = colors[color]
        vertices = triangles.reshape(-1, 3) @ rotation.T
        mesh = trimesh.Trimesh(vertices=vertices, faces=np.arange(len(vertices)).reshape(-1, 3), process=False)
        mesh.visual.vertex_colors = rgba
        meshes.append(mesh)
    if not meshes:
        raise ValueError(f'LDraw 模型没有可渲染的三角面：{source}')
    print(f'LDraw: {source.name}; library={root}; triangles={sum(len(m.faces) for m in meshes)}; colors={len(meshes)}', flush=True)
    return meshes
