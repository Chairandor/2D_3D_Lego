"""无硬件回归：SDK ABI、彩色格式、相机选项；不代替 Gemini 实机验证。"""
import ctypes as ct
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from camera_source import CameraParameters, GeminiCamera, decode_color, find_gemini_sdk


class CameraSourceTests(unittest.TestCase):
    def test_sdk_183_packed_camera_parameters(self):
        self.assertEqual(ct.sizeof(CameraParameters), 153)
        self.assertEqual(CameraParameters.rgb.offset, 20)
        self.assertEqual(CameraParameters.rgb_distortion.offset, 72)

    def test_rgb_padding_and_channel_order(self):
        raw = bytes([255, 0, 0, 0, 255, 0, 99, 99, 0, 0, 255, 255, 255, 255, 99, 99])
        frame = decode_color(raw, 2, 2, 22)
        np.testing.assert_array_equal(frame[0, 0], [0, 0, 255])
        np.testing.assert_array_equal(frame[0, 1], [0, 255, 0])
        np.testing.assert_array_equal(frame[1, 0], [255, 0, 0])
        self.assertEqual(frame.shape, (2, 2, 3))

    def test_color_intrinsics_are_not_depth_intrinsics(self):
        camera = GeminiCamera.__new__(GeminiCamera)
        camera.info = dict(width=640, height=480)
        camera.camera_parameters = CameraParameters()
        params = camera.camera_parameters
        params.depth.fx = 999
        params.rgb.fx, params.rgb.fy = 600, 601
        params.rgb.cx, params.rgb.cy = 320, 240
        params.rgb.width, params.rgb.height = 640, 480
        params.rgb_distortion.k1, params.rgb_distortion.p1 = 0.2, 0.01
        K, dist = camera.intrinsics()
        self.assertEqual(K[0, 0], 600)
        self.assertEqual(K[1, 1], 601)
        self.assertAlmostEqual(dist[2], 0.01)
        camera.info['height'] = 360
        self.assertIsNone(camera.intrinsics())

    def test_jpeg_and_yuyv_color_streams(self):
        image = np.full((24, 32, 3), [40, 80, 160], dtype=np.uint8)
        encoded = cv2.imencode('.jpg', image)[1].tobytes()
        decoded = decode_color(encoded, 32, 24, 5)
        np.testing.assert_allclose(decoded.mean(axis=(0, 1)), [40, 80, 160], atol=3)
        gray = decode_color(bytes([128, 128, 128, 128] * 2), 2, 2, 0)
        self.assertLess(int(np.ptp(gray[0, 0])), 2)
        with self.assertRaises(ValueError):
            decode_color(b'bad', 640, 480, 22)
        with self.assertRaises(ValueError):
            decode_color(b'bad', 640, 480, 6)

    def test_explicit_bad_sdk_path_does_not_silently_select_lab_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                find_gemini_sdk(str(Path(directory) / 'missing.dll'))


if __name__ == '__main__':
    unittest.main()
