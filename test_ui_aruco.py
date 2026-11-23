"""验证 AR 参数、文件边界与源文件保护，不用 mock 代替摄像头测试。"""
from pathlib import Path
import tempfile
import unittest

from ui_aruco import ArucoService, validate_camera_options


class ArucoInputTests(unittest.TestCase):
    def test_reject_non_finite_and_fractional_camera_options(self) -> None:
        for key, value in [('marker_size_cm', 'nan'), ('fit_width_cm', 'inf'),
                           ('marker_size_cm', 0), ('camera', 0.5), ('marker_id', 50),
                           ('gesture_angle', float('nan')), ('gesture_angle', 100), ('gestures', 'false'),
                           ('camera_source', 'astra')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_camera_options({key: value})

    def test_gesture_options_and_reset_require_live_session(self) -> None:
        options = validate_camera_options({'gestures': False, 'show_skeleton': False, 'gesture_angle': 45})
        self.assertFalse(options['gestures'])
        self.assertFalse(options['show_skeleton'])
        self.assertEqual(options['gesture_angle'], 45)
        self.assertEqual(options['camera_source'], 'gemini')
        self.assertEqual(validate_camera_options({'camera_source':'opencv'})['camera_source'], 'opencv')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, '启动摄像头'):
                ArucoService(Path(directory)).reset_rotation()

    def test_upload_rejects_bad_extension_and_encoding_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ArucoService(root)
            for name, raw in [('model.exe', b'0 model'), ('bad.ldr', b'\xff'), ('empty.ldr', b'')]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    service.upload(name, raw)
            self.assertFalse((root / 'ui_uploads').exists())

    def test_cannot_start_camera_without_prepared_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ArucoService(Path(directory))
            with self.assertRaisesRegex(ValueError, '选择模型'):
                service.start({})
            self.assertIsNone(service.process)

    def test_assets_do_not_accept_disk_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ArucoService(Path(directory))
            self.assertIsNone(service.asset('../../config.local.json'))
            self.assertIsNone(service.asset('model'))
            service.stop()  # Stopping an idle task must not affect other jobs.


if __name__ == '__main__':
    unittest.main()
