"""在独立 AR 环境读取真实 LDraw 几何，生成工作台使用的 GLB 预览。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def main() -> None:
    """生成 Y-up 网页预览，保持源模型文件不变。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ar-root', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ldraw-dir', default='')
    parser.add_argument('--studio-colors', default='')
    args = parser.parse_args()
    sys.path.insert(0, str(args.ar_root.resolve()))
    from aruco_lego_ar import load_lego_meshes
    import numpy as np
    import trimesh

    digest = hashlib.sha256(args.model.read_bytes()).hexdigest()
    meshes = load_lego_meshes(str(args.model), 'y', 0.09, args.ldraw_dir, args.studio_colors)
    bounds = np.stack([mesh.bounds for mesh in meshes])
    lower, upper = bounds[:, 0].min(axis=0), bounds[:, 1].max(axis=0)
    scene = trimesh.Scene()
    z_to_y = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float)
    for index, mesh in enumerate(meshes):
        mesh.apply_transform(z_to_y)
        # Double-sided surfaces match the AR preview for LDraw BFC variants.
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=mesh.visual.vertex_colors[0], metallicFactor=0,
            roughnessFactor=0.75, doubleSided=True))
        scene.add_geometry(mesh, node_name=f'color_{index}')
    args.output.mkdir(parents=True, exist_ok=True)
    scene.export(args.output / 'preview.glb')
    if hashlib.sha256(args.model.read_bytes()).hexdigest() != digest:
        raise RuntimeError('预览生成期间源模型发生变化，请重新载入。')
    report = dict(source=str(args.model.resolve()), source_sha256=digest,
                  triangles=sum(len(mesh.faces) for mesh in meshes), colors=len(meshes),
                  display_size_cm=((upper - lower) * 100).tolist(), preview_up_axis='y')
    (args.output / 'model.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PREVIEW READY', flush=True)


if __name__ == '__main__':
    main()
