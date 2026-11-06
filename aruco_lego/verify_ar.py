"""Verify real model loading, printed-marker detection and OpenGL composition.

This synthetic-image test does not claim a physical camera/printed-marker pass.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import pyrender

from aruco_lego_ar import ARUCO_DICT, CV_TO_GL, camera_matrix_for_frame, load_lego_meshes


def main() -> None:
    """Save synthetic AR evidence for each selected real LDraw model."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--studio-colors', default='')
    parser.add_argument('models', nargs='+', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    width, height, size = 640, 480, 0.06
    marker = cv2.imread(str(Path(__file__).with_name('aruco_id0.png')), cv2.IMREAD_GRAYSCALE)
    detected, ids, _ = cv2.aruco.ArucoDetector(ARUCO_DICT).detectMarkers(marker)
    if ids is None or 0 not in ids:
        raise RuntimeError('附带标记图片未识别到 ID 0。')
    index = list(ids.flatten()).index(0)
    image_points = np.array([[285, 290], [355, 285], [362, 350], [278, 355]], dtype=np.float32)
    homography = cv2.getPerspectiveTransform(detected[index].reshape(4, 2), image_points)
    background = cv2.warpPerspective(marker, homography, (width, height), borderValue=255)
    corners, ids, _ = cv2.aruco.ArucoDetector(ARUCO_DICT).detectMarkers(background)
    if ids is None or 0 not in ids:
        raise RuntimeError('透视变换后的标记未识别到 ID 0。')
    image_points = corners[list(ids.flatten()).index(0)].reshape(4, 2)
    object_points = np.array([[-size/2, size/2, 0], [size/2, size/2, 0],
                              [size/2, -size/2, 0], [-size/2, -size/2, 0]], dtype=np.float32)
    K, dist, _ = camera_matrix_for_frame(width, height, '')
    found, rvec, tvec = cv2.solvePnP(object_points, image_points, K, dist)
    if not found:
        raise RuntimeError('标记位姿求解失败。')
    pose = np.eye(4)
    pose[:3, :3] = cv2.Rodrigues(rvec)[0]
    pose[:3, 3] = tvec.ravel()
    renderer = pyrender.OffscreenRenderer(width, height)
    report = {'test_kind': 'synthetic_marker_real_model_opengl', 'physical_camera_test': False, 'models': []}
    try:
        for path in args.models:
            meshes = load_lego_meshes(str(path), 'y', 0.09, studio_colors=args.studio_colors)
            vertices = np.concatenate([mesh.vertices for mesh in meshes])
            assert np.isclose(vertices[:, 2].min(), 0), '模型底面应在标记平面。'
            assert np.isclose(np.ptp(vertices[:, :2], axis=0).max(), 0.09), '模型水平缩放错误。'
            scene = pyrender.Scene(ambient_light=[0.35]*3)
            model = pyrender.Mesh.from_trimesh(meshes, smooth=False)
            for primitive in model.primitives:
                primitive.material.doubleSided = True
            scene.add(model, pose=CV_TO_GL @ pose)
            scene.add(pyrender.IntrinsicsCamera(K[0,0], K[1,1], K[0,2], K[1,2], znear=0.005))
            scene.add(pyrender.DirectionalLight(color=np.ones(3), intensity=3))
            color, depth = renderer.render(scene)
            mask = depth > 0
            pixels = int(mask.sum())
            assert pixels > 100, 'OpenGL 未输出有效模型像素。'
            frame = cv2.cvtColor(background, cv2.COLOR_GRAY2BGR)
            frame[mask] = cv2.cvtColor(color, cv2.COLOR_RGB2BGR)[mask]
            # imencode handles Windows Unicode paths consistently.
            cv2.imencode('.png', frame)[1].tofile(str(args.output / f'{path.stem}_synthetic.png'))
            result = dict(source=str(path.resolve()), triangles=sum(len(m.faces) for m in meshes),
                          colors=len(meshes), marker_id=0, overlay_pixels=pixels,
                          bounds_m=[vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()])
            report['models'].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        renderer.delete()
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
