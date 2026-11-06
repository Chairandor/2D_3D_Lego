"""Marker-based Lego AR: OpenCV ArUco + Trimesh + Pyrender.

Quick start:
  pip install opencv-contrib-python numpy trimesh pyrender 'pyglet<2'
  python aruco_lego_ar.py
  python aruco_lego_ar.py --model lego.glb --up-axis y

Print aruco_id0.png and measure its outer black-square edge. By default
it should be 0.06 m (6 cm). Use --marker-size to specify actual size.

Optional calibration.npz must contain K (3x3) and dist (distortion).
It should be calibrated for the same video resolution/focus setting.
"""
import argparse
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
import trimesh

# In Ubuntu/headless environments, set PYOPENGL_PLATFORM=egl BEFORE import pyrender.
import pyrender
from ldraw_mesh import load_ldraw

ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
CV_TO_GL = np.diag([1.0, -1.0, -1.0, 1.0])


def create_marker(path: Path):
    marker = cv2.aruco.generateImageMarker(ARUCO_DICT, 0, 600)
    marker = cv2.copyMakeBorder(marker, 75, 75, 75, 75,
                                cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite(str(path), marker)


def demo_brick():
    # Simple 2x4 brick in METERS, resting on z=0.
    body = trimesh.creation.box(extents=(0.08, 0.04, 0.018))
    body.apply_translation([0, 0, 0.009])
    meshes = [body]
    for x in (-0.03, -0.01, 0.01, 0.03):
        for y in (-0.01, 0.01):
            stud = trimesh.creation.cylinder(radius=0.006, height=0.004, sections=24)
            stud.apply_translation([x, y, 0.020])
            meshes.append(stud)
    for mesh in meshes:
        mesh.visual.vertex_colors = np.array([210, 45, 50, 255], dtype=np.uint8)
    return meshes


def load_lego_meshes(path: str, up_axis: str, fit_width: float, ldraw_dir: str = '', studio_colors: str = '') -> list[trimesh.Trimesh]:
    """Load colored geometry and fit its footprint onto the marker plane."""
    if not path:
        return demo_brick()

    # Load entire scene, including sub-model node transforms and materials.
    meshes = []
    up_transform = np.eye(4)
    if up_axis == 'y':  # glTF/GLB usually uses +Y up; make local +Z up.
        up_transform[:3, :3] = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
            [0.0, 1.0, 0.0]
        ])

    if Path(path).suffix.lower() in ('.ldr', '.mpd'):
        meshes = load_ldraw(path, ldraw_dir, studio_colors)  # Already converted from LDraw -Y to +Z.
    else:
        source = trimesh.load(path, force='scene')
        for node_name in source.graph.nodes_geometry:
            transform, geometry_name = source.graph[node_name]
            mesh = source.geometry[geometry_name].copy()
            if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) == 0:
                continue
            mesh.apply_transform(transform)
            mesh.apply_transform(up_transform)
            meshes.append(mesh)
    if not meshes:
        raise ValueError('Model contains no triangle meshes; export to GLB/OBJ/STL first.')

    all_vertices = np.concatenate([m.vertices for m in meshes], axis=0)
    min_corner = all_vertices.min(axis=0)
    max_corner = all_vertices.max(axis=0)
    # Center x/y over ArUco. Make the bottom touch marker plane (z=0).
    origin = np.array([(min_corner[0] + max_corner[0]) * .5,
                       (min_corner[1] + max_corner[1]) * .5,
                       min_corner[2]])
    horizontal_extent = max(max_corner[0] - min_corner[0],
                            max_corner[1] - min_corner[1])
    if horizontal_extent <= 0:
        raise ValueError('Model is degenerate (zero horizontal extent).')
    scale = fit_width / horizontal_extent
    for mesh in meshes:
        mesh.vertices = (mesh.vertices - origin) * scale
    return meshes


def camera_matrix_for_frame(width, height, calib_path):
    if calib_path:
        data = np.load(calib_path)
        K = data['K'].astype(np.float64)
        dist = data['dist'].astype(np.float64)
        if 'size' in data and not np.array_equal(np.asarray(data['size']), [width, height]):
            print('WARNING: calibration size differs from current frame resolution.')
        return K, dist, True
    # Approximate focal length: enough for a demo, NOT for accurate AR.
    f = 0.9 * max(width, height)
    K = np.array([[f, 0.0, width / 2.0],
                  [0.0, f, height / 2.0],
                  [0.0, 0.0, 1.0]], dtype=np.float64)
    return K, np.zeros(5), False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='', help='LDR/MPD/GLB/OBJ/STL path; empty = red demo brick')
    parser.add_argument('--ldraw-dir', default='', help='Local LDraw library (parts, p, LDConfig.ldr)')
    parser.add_argument('--studio-colors', default='', help='Optional StudioColorDefinition.txt for Studio-only colors')
    parser.add_argument('--up-axis', choices=['y', 'z'], default='y',
                        help='Model original up-axis: GLB usually y, OBJ usually z')
    parser.add_argument('--marker-size', type=float, default=0.06,
                        help='Measured outside black square edge, meters')
    parser.add_argument('--fit-width', type=float, default=0.09,
                        help='Target model maximum horizontal extent in meters')
    parser.add_argument('--calibration', default='', help='Optional calibration.npz with K and dist')
    parser.add_argument('--camera', type=int, default=0, help='OpenCV camera index')
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--marker-id', type=int, default=0, help='Target ID in DICT_4X4_50')
    parser.add_argument('--camera-api', choices=['auto', 'dshow', 'msmf'], default='auto')
    parser.add_argument('--log-dir', type=Path, help='Save run status and first successful AR frame locally')
    parser.add_argument('--max-seconds', type=float, default=0, help='Optional timed camera test; 0 = until q')
    parser.add_argument('--no-window', action='store_true', help='Stream to the integrated web UI without an OpenCV window')
    parser.add_argument('--frame-output', type=Path, help='Atomically update a local JPEG for the web UI')
    parser.add_argument('--stop-file', type=Path, help='Stop and release the camera when this file exists')
    args = parser.parse_args()
    if args.marker_size <= 0 or args.fit_width <= 0 or not 0 <= args.marker_id < 50:
        parser.error('marker-size 和 fit-width 必须为正数；marker-id 范围为 0–49。')
    if args.log_dir:
        args.log_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    status = dict(model=str(Path(args.model).resolve()) if args.model else 'demo',
                  marker_id=args.marker_id, marker_size_m=args.marker_size,
                  camera=args.camera, camera_api=args.camera_api,
                  camera_opened=False, frames=0, detected_frames=0, rendered_frames=0,
                  detected_ids=[], current_ids=[], overlay_pixels=0, error=None)

    def save_status() -> None:
        if args.log_dir:
            status['elapsed_seconds'] = round(time.monotonic() - started, 2)
            temporary = args.log_dir / 'status.tmp'
            temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(args.log_dir / 'status.json')

    marker_path = Path(__file__).resolve().parent / 'aruco_id0.png'
    if not marker_path.exists():
        create_marker(marker_path)
        print(f'Created {marker_path}. Print it and measure the BLACK square width.')

    # Load before taking control of the camera, so missing parts fail promptly.
    try:
        mesh_list = load_lego_meshes(args.model, args.up_axis, args.fit_width, args.ldraw_dir, args.studio_colors)
    except Exception as error:
        status['error'] = str(error)
        save_status()
        raise
    apis = {'auto': cv2.CAP_ANY, 'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF}
    api_names = [args.camera_api] if args.camera_api != 'auto' else (['dshow', 'msmf'] if os.name == 'nt' else ['auto'])
    cap = None
    frame = None
    for api_name in api_names:
        candidate = cv2.VideoCapture(args.camera, apis[api_name])
        candidate.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        candidate.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        if candidate.isOpened():
            for _ in range(15):
                ok, frame = candidate.read()
                if ok:
                    cap = candidate
                    break
        if cap is not None:
            status['camera_api'] = api_name
            break
        candidate.release()
    if cap is None:
        status['error'] = f'无法从摄像头 {args.camera} 读取画面。关闭占用相机的应用，检查 Windows 相机权限，或尝试 --camera 1。'
        save_status()
        raise RuntimeError(status['error'])
    status['camera_opened'] = True
    print(f'Camera {args.camera}: {status["camera_api"]}, {frame.shape[1]}x{frame.shape[0]}', flush=True)
    height, width = frame.shape[:2]

    K, dist, calibrated = camera_matrix_for_frame(width, height, args.calibration)
    if not calibrated:
        print('WARNING: Using estimated camera intrinsics. For stable/accurate AR, calibrate camera.')

    detector_parameters = cv2.aruco.DetectorParameters()
    detector_parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(ARUCO_DICT, detector_parameters)

    s = args.marker_size
    marker_corners_3d = np.array([
        [-s / 2, +s / 2, 0],
        [+s / 2, +s / 2, 0],
        [+s / 2, -s / 2, 0],
        [-s / 2, -s / 2, 0]
    ], dtype=np.float32)

    scene = pyrender.Scene(bg_color=[0.0, 0.0, 0.0, 0.0],
                           ambient_light=[0.35, 0.35, 0.35])
    # One mesh with multiple primitives can retain materials from separate parts.
    model = pyrender.Mesh.from_trimesh(mesh_list, smooth=False)
    for primitive in model.primitives:
        primitive.material.doubleSided = True
    model_node = scene.add(model, pose=np.eye(4))

    camera = pyrender.IntrinsicsCamera(K[0, 0], K[1, 1], K[0, 2], K[1, 2],
                                       znear=0.005, zfar=20.0)
    scene.add(camera, pose=np.eye(4))
    light = pyrender.DirectionalLight(color=np.ones(3), intensity=3.0)
    scene.add(light, pose=np.eye(4))
    renderer = None
    last_saved = 0.0
    last_frame_saved = 0.0
    try:
        renderer = pyrender.OffscreenRenderer(viewport_width=width, viewport_height=height)
        print(f'Point camera at marker ID {args.marker_id}. Press q or ESC to exit.', flush=True)
        while True:
            if args.stop_file and args.stop_file.exists():
                break
            if args.max_seconds and time.monotonic() - started >= args.max_seconds:
                break
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError('摄像头视频流中断，请检查连接或是否被其他程序占用。')
            status['frames'] += 1
            # Render and detect on rectified image. This matches pyrender's
            # pinhole camera, which does not model lens distortion.
            frame = cv2.undistort(frame, K, dist) if calibrated else frame
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            corners, ids, _ = detector.detectMarkers(gray)
            visible_pixels = 0
            status['current_ids'] = list(map(int, ids.flatten())) if ids is not None else []
            if ids is not None:
                status['detected_ids'] = sorted(set(status['detected_ids']) | set(map(int, ids.flatten())))
                cv2.aruco.drawDetectedMarkers(frame, corners, ids)

            if ids is not None and args.marker_id in ids.flatten():
                status['detected_frames'] += 1
                idx = int(np.where(ids.flatten() == args.marker_id)[0][0])
                image_pts = corners[idx].reshape(4, 2).astype(np.float32)
                found, rvec, tvec = cv2.solvePnP(
                    marker_corners_3d, image_pts, K, np.zeros(5),
                    flags=cv2.SOLVEPNP_ITERATIVE)
                if found:
                    R, _ = cv2.Rodrigues(rvec)
                    T_marker_to_cv = np.eye(4)
                    T_marker_to_cv[:3, :3] = R
                    T_marker_to_cv[:3, 3] = tvec.reshape(3)
                    # Convert camera axes OpenCV (+x right,+y down,+z forward)
                    # to OpenGL (+x right,+y up,-z forward).
                    T_marker_to_gl = CV_TO_GL @ T_marker_to_cv
                    scene.set_pose(model_node, pose=T_marker_to_gl)
                    color_rgb, depth = renderer.render(scene)
                    visible = depth > 0
                    visible_pixels = int(visible.sum())
                    if visible_pixels:
                        status['rendered_frames'] += 1
                        if status['rendered_frames'] == 1:
                            print(f'AR SUCCESS: detected ID {args.marker_id}, overlay={visible_pixels} pixels', flush=True)
                    color_bgr = cv2.cvtColor(color_rgb, cv2.COLOR_RGB2BGR)
                    frame[visible] = color_bgr[visible]
                    cv2.drawFrameAxes(frame, K, np.zeros(5), rvec, tvec,
                                     s * 0.6, 2)

            label = f'ID {args.marker_id}: ' + ('AR OK' if visible_pixels else 'searching')
            status['overlay_pixels'] = visible_pixels
            cv2.putText(frame, f'ArUco LEGO AR | {label}' + (' | q: quit' if not args.no_window else ''), (15, 27),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            if args.log_dir and visible_pixels and status['rendered_frames'] == 1:
                cv2.imwrite(str(args.log_dir / 'first_ar_frame.png'), frame)
            if time.monotonic() - last_saved >= (0.5 if args.no_window else 2):
                save_status()
                last_saved = time.monotonic()
            if args.frame_output and time.monotonic() - last_frame_saved >= 0.1:
                temporary = args.frame_output.with_suffix('.tmp')
                encoded, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if encoded:
                    temporary.write_bytes(jpeg.tobytes())
                    try:
                        temporary.replace(args.frame_output)
                    except PermissionError:
                        pass  # A Windows HTTP reader may still have the preceding frame open.
                last_frame_saved = time.monotonic()
            if not args.no_window:
                cv2.imshow('LEGO AR', frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('q'), 27):
                    break
                if cv2.getWindowProperty('LEGO AR', cv2.WND_PROP_VISIBLE) < 1:
                    break
    except Exception as error:
        status['error'] = str(error)
        raise
    finally:
        save_status()
        if renderer is not None:
            renderer.delete()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
