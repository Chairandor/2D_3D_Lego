"""Optional: calibrate camera using a chessboard.

A board with 10x7 chess squares (9x6 inner corners) is required.
Press SPACE with the board visible at different positions/tilts, collect
at least 15 images; press c to compute/save, q to quit.

Example: python calibrate_camera.py --camera 0 --square-size 0.018
"""
import argparse
from pathlib import Path

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--square-size', type=float, default=0.018,
                        help='Printed chess square edge (m)')
    parser.add_argument('--output', default='calibration.npz')
    args = parser.parse_args()
    board_size = (9, 6)  # inner corners
    obj = np.zeros((board_size[0] * board_size[1], 3), dtype=np.float32)
    obj[:, :2] = np.mgrid[0:board_size[0], 0:board_size[1]].T.reshape(-1, 2)
    obj *= args.square_size
    obj_points, image_points = [], []

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        raise RuntimeError('Cannot open camera')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    image_size = None
    print('SPACE: capture a DIFFERENT board pose; C: calibrate; Q: quit.')
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            image_size = (gray.shape[1], gray.shape[0])
            found, corners = cv2.findChessboardCorners(gray, board_size)
            if found:
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                            30, 0.001)
                corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
                cv2.drawChessboardCorners(image, board_size, corners, found)
            cv2.putText(image, f'Views: {len(obj_points)} | SPACE: capture | C: finish',
                        (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 220, 0) if found else (0, 0, 255), 2)
            cv2.imshow('Camera calibration', image)
            key = cv2.waitKey(1) & 0xFF
            if key == 32 and found:
                obj_points.append(obj.copy())
                image_points.append(corners.copy())
                print(f'Captured view {len(obj_points)}')
            elif key == ord('c'):
                if len(obj_points) < 10:
                    print('Collect at least 10 views, ideally 15-25 with varied tilt/distance.')
                    continue
                rms, K, dist, _, _ = cv2.calibrateCamera(
                    obj_points, image_points, image_size, None, None)
                np.savez(args.output, K=K, dist=dist, size=np.array(image_size))
                print(f'Saved {args.output}, RMS reprojection error: {rms:.3f} pixels')
                print('K =\n', K)
                break
            elif key in (ord('q'), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
