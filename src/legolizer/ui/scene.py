"""生成浏览器轻量预览；不改变源文件，简化/包围盒近似均显式标记。"""

from __future__ import annotations

import math
from pathlib import Path

from legolizer.constants import LDU_PER_MM

BOX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
# LDraw 标准 Brick/Plate/Tile 的本体包围尺寸（stud X/Z、plate 高），不含凸点。
PARTS = {
    "3005": (1, 1, 3), "3004": (1, 2, 3), "3622": (1, 3, 3), "3010": (1, 4, 3),
    "3009": (1, 6, 3), "3008": (1, 8, 3), "3003": (2, 2, 3), "3002": (2, 3, 3),
    "3001": (2, 4, 3), "2456": (2, 6, 3), "3007": (2, 8, 3),
    "3024": (1, 1, 1), "3023": (1, 2, 1), "3623": (1, 3, 1), "3710": (1, 4, 1),
    "3666": (1, 6, 1), "3460": (1, 8, 1), "3022": (2, 2, 1), "3021": (2, 3, 1),
    "3020": (2, 4, 1), "3795": (2, 6, 1), "3034": (2, 8, 1),
    "3070b": (1, 1, 1), "3069b": (1, 2, 1), "63864": (1, 3, 1), "2431": (1, 4, 1),
    "3068b": (2, 2, 1), "87079": (2, 4, 1),
}
PALETTE = {0: [27, 42, 52], 1: [30, 90, 168], 2: [0, 133, 43], 4: [201, 26, 9],
           7: [138, 146, 141], 9: [180, 210, 227], 14: [242, 205, 55], 15: [244, 244, 244],
           19: [228, 205, 158], 25: [254, 138, 24], 70: [88, 42, 18], 71: [160, 165, 169],
           72: [108, 110, 104], 73: [90, 147, 219], 272: [10, 52, 99], 320: [114, 14, 15]}


def box_vertices(lo: list, hi: list) -> list:
    return [[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]],
            [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
            [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]],
            [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]]


def append_box(data: dict, vertices: list, color: list, step: int) -> None:
    offset = len(data["vertices"])
    data["vertices"].extend(vertices)
    data["faces"].extend([[offset + i for i in face] for face in BOX_FACES])
    data["colors"].extend([color] * len(BOX_FACES))
    data["steps"].extend([step] * len(BOX_FACES))


def ldraw_scene(path: Path) -> dict:
    """递归展开 MPD 子模型并转换 LDraw -Y-up；未知零件不猜尺寸。"""
    models: dict[str, list[str]] = {}
    current = "__root__"
    models[current] = []
    first = None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.upper().startswith("0 FILE "):
            current = line[7:].strip().lower()
            first = first or current
            models.setdefault(current, [])
        else:
            models[current].append(line)
    data = dict(kind="bricks", vertices=[], faces=[], colors=[], steps=[], max_step=1)
    skipped: set[str] = set()
    count = 0
    displayed = 0
    identity = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]

    def transform(matrix, point, translation):
        return [sum(matrix[i][j] * point[j] for j in range(3)) + translation[i] for i in range(3)]

    def walk(name, rotation, translation, inherited_color=7, inherited_step=None, stack=()):
        nonlocal count, displayed
        if name in stack or len(stack) > 20:
            raise ValueError("MPD 子模型循环引用或嵌套过深")
        step = 1
        for line in models[name]:
            parts = line.split()
            if parts[:2] in (["0", "STEP"], ["0", "ROTSTEP"]):
                step += 1
            if not parts or parts[0] != "1":
                continue
            if len(parts) < 15:
                raise ValueError("LDraw 零件行不完整")
            numbers = list(map(float, parts[2:14]))
            if not all(math.isfinite(n) for n in numbers):
                raise ValueError("LDraw 包含非有限变换")
            color = int(parts[1])
            color = inherited_color if color == 16 else color
            local = [numbers[3 + i * 3:6 + i * 3] for i in range(3)]
            composed = [[sum(rotation[i][k] * local[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
            position = transform(rotation, numbers[:3], translation)
            part = " ".join(parts[14:]).lower()
            show_step = inherited_step or step
            data["max_step"] = max(data["max_step"], show_step)
            if part in models:
                walk(part, composed, position, color, show_step, (*stack, name))
                continue
            count += 1
            spec = PARTS.get(Path(part).stem)
            if not spec or displayed >= 6000:
                skipped.add(part)
                continue
            width, depth, height = spec
            # 零件原点位于顶部，LDraw 的正 Y 朝下；0.4 LDU 缝隙用于辨识砖边。
            corners = box_vertices([-width * 10 + .4, 0, -depth * 10 + .4],
                                   [width * 10 - .4, height * 8 - .4, depth * 10 - .4])
            world = [transform(composed, p, position) for p in corners]
            vertices = [[p[0] / LDU_PER_MM, p[2] / LDU_PER_MM, -p[1] / LDU_PER_MM] for p in world]
            append_box(data, vertices, PALETTE.get(color, [99, 167, 197]), show_step)
            displayed += 1
    walk(first or "__root__", identity, [0, 0, 0])
    if not data["vertices"]:
        raise ValueError("没有可预览的标准砖零件；请下载后用 BrickLink Studio 查看")
    data["stats"] = {"零件实例": count, "显示零件": displayed, "文件步骤": data["max_step"], "坐标": "mm / Z-up"}
    data["note"] = "标准砖本体包围盒预览，不含凸点；步骤来自文件，不代表装配已认证。"
    if skipped:
        data["note"] += f" 未显示 {count - displayed} 件（未知零件或超过 6000 件上限）：" + ", ".join(sorted(skipped)[:12])
    return data


def scene(path: Path, axis: str = "z") -> dict:
    """返回实际几何的有限预览数据；Mesh 最多 8000 面、点云最多 12000 点。"""
    if path.suffix.lower() in {".ldr", ".mpd"}:
        return ldraw_scene(path)
    import numpy as np
    import open3d as o3d
    from legolizer.processing3d.orientation import orientation_matrix
    from legolizer.processing3d.colors import COMMON_LDRAW_COLORS
    palette = {c.code: list(c.rgb) for c in COMMON_LDRAW_COLORS}
    data = dict(kind="mesh", vertices=[], faces=[], colors=[], steps=[], max_step=1)
    if path.suffix.lower() == ".npz":
        from legolizer.processing3d.volume import VoxelVolume
        if axis != "z":
            raise ValueError("项目 NPZ 已经是 Z-up，请把预览上轴改为 Z")
        volume = VoxelVolume.load(path)
        cells = np.argwhere(volume.occupancy)
        total = len(cells)
        if total > 5000:
            cells = cells[np.linspace(0, total - 1, 5000, dtype=int)]
        size = np.asarray(volume.voxel_size_mm)
        origin = np.asarray(volume.origin_mm)
        for cell in cells:
            lo = origin + cell * size
            hi = lo + size * 0.96
            color = palette.get(int(volume.colors[tuple(cell)]), [85, 171, 181]) if volume.colors is not None else [85, 171, 181]
            append_box(data, box_vertices(lo.tolist(), hi.tolist()), color, 1)
        data["kind"] = "voxel"
        data["stats"] = {"占用格数": total, "显示格数": len(cells), "体素尺寸 / mm": list(volume.voxel_size_mm), "语义": volume.semantic}
        data["note"] = "体素几何预览（mm / Z-up）。" + ("已均匀抽样至 5000 格，局部会缺格。" if total > 5000 else "")
        return data
    suffix = path.suffix.lower()
    if suffix not in {".ply", ".stl", ".obj", ".off", ".gltf", ".glb", ".pcd", ".xyz", ".xyzn", ".xyzrgb", ".pts"}:
        raise ValueError("此文件是报告、BOM 或配置，请下载查看；不是可预览几何")
    if axis not in {"x", "-x", "y", "-y", "z", "-z"}:
        raise ValueError("预览请选择明确的源上轴")
    mesh_input = suffix in {".stl", ".obj", ".off", ".gltf", ".glb"}
    if suffix == ".ply":
        with path.open("rb") as handle:
            mesh_input = b"element face " in handle.read(16384).split(b"end_header", 1)[0]
    geometry = o3d.io.read_triangle_mesh(str(path)) if mesh_input else o3d.io.read_point_cloud(str(path))
    geometry.transform(orientation_matrix(axis))
    if mesh_input:
        if not geometry.has_triangles():
            raise ValueError("网格为空或读取失败")
        count = len(geometry.triangles)
        if count > 8000:
            geometry = geometry.simplify_quadric_decimation(8000)
        points = np.asarray(geometry.vertices)
        faces = np.asarray(geometry.triangles)
        data["faces"] = faces.tolist()
        data["colors"] = np.rint(np.asarray(geometry.vertex_colors)[faces].mean(axis=1) * 255).astype(int).tolist() if geometry.has_vertex_colors() else []
        data["stats"] = {"源三角面": count, "预览三角面": len(faces), "源上轴": axis.upper()}
        data["note"] = "按源坐标单位显示；预览旋转不改动源文件。" + ("为交互简化至约 8000 面；纹理贴图不在此渲染。" if count > 8000 else "纹理贴图不在此渲染。")
    else:
        points = np.asarray(geometry.points)
        if not len(points):
            raise ValueError("点云为空或读取失败")
        count = len(points)
        indices = np.linspace(0, count - 1, min(count, 12000), dtype=int)
        points = points[indices]
        data["kind"] = "pointcloud"
        data["colors"] = np.rint(np.asarray(geometry.colors)[indices] * 255).astype(int).tolist() if geometry.has_colors() else []
        data["stats"] = {"源点数": count, "预览点数": len(points), "源上轴": axis.upper()}
        data["note"] = "表面点预览，最多 12000 点；按源坐标单位显示。"
    if not np.isfinite(points).all():
        raise ValueError("几何包含 NaN 或无穷坐标")
    data["vertices"] = np.round(points, 7).tolist()
    data["stats"]["尺寸 / 源单位"] = np.round(points.max(axis=0) - points.min(axis=0), 4).tolist()
    return data
