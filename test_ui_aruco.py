"""验证 AR 参数、文件边界与源文件保护，不用 mock 代替摄像头测试。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import pipeline_ui
from ui_aruco import ArucoService, validate_camera_options


class ArucoInputTests(unittest.TestCase):
    def test_reject_non_finite_and_fractional_camera_options(self) -> None:
        for key, value in [('marker_size_cm', 'nan'), ('fit_width_cm', 'inf'),
                           ('marker_size_cm', 0), ('camera', 0.5), ('marker_id', 50),
                           ('gesture_speed', float('nan')), ('gesture_speed', 181), ('gestures', 'false'),
                           ('camera_source', 'astra')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_camera_options({key: value})

    def test_gesture_options_and_reset_require_live_session(self) -> None:
        options = validate_camera_options({'gestures': False, 'show_skeleton': False, 'gesture_angle': 45})
        self.assertFalse(options['gestures'])
        self.assertFalse(options['show_skeleton'])
        self.assertEqual(options['gesture_speed'], 45)
        self.assertEqual(validate_camera_options({})['gesture_speed'], 60)
        self.assertEqual(validate_camera_options({'gesture_speed':120})['gesture_speed'], 120)
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

    def test_board_accepts_two_models_and_uses_a_five_centimetre_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            service = ArucoService(root)
            model = root / 'model.mpd'
            model.write_text('0 fixture', encoding='utf-8')
            with patch.object(service, '_launch') as launch, patch.object(service, '_dependency_args', return_value=[]):
                state = service.start_board({})
            command = launch.call_args.args[0]
            self.assertIn('--board', command)
            self.assertNotIn('--model', command)
            self.assertEqual(command[command.index('--marker-size') + 1], '0.05')
            self.assertTrue(state['board_mode'])
            service.phase = 'running'
            service.live_dir.mkdir(parents=True)
            with patch.object(service, '_busy', return_value=True):
                service.place_on_board(model, '第一份', {'x_cm': -2, 'y_cm': 5, 'angle': 90})
                service.place_on_board(model, '第二份', {'x_cm': 3, 'y_cm': 6, 'angle': -15})
                with self.assertRaises(ValueError):
                    service.place_on_board(model, '错误输入', {'x_cm': 'nan', 'y_cm': 5, 'angle': 0})
            entries = json.loads((service.live_dir / 'control.json').read_text(encoding='utf-8'))['placements']
            self.assertEqual([(p['x_cm'], p['y_cm'], p['angle']) for p in entries], [(-2, 5, 90), (3, 6, 345)])
            self.assertNotEqual(entries[0]['id'], entries[1]['id'])

    def test_board_only_accepts_lego_cards_from_the_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = (Path(directory) / 'test.mpd').resolve()
            path.touch()
            url = '/file?' + urlencode({'path': str(path)})
            status = {'artifact_groups': [{'original': {'studs': 8, 'label': 'LEGO', 'url': url}}]}
            with patch.object(pipeline_ui.CONTROLLER, 'status', return_value=status), \
                    patch.object(pipeline_ui, 'downloadable_file', return_value=True):
                self.assertEqual(pipeline_ui.board_model(url), (path, 'LEGO'))
                with self.assertRaises(ValueError):
                    pipeline_ui.board_model('/file?path=previous.mpd')

    def test_assets_do_not_accept_disk_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ArucoService(Path(directory))
            self.assertIsNone(service.asset('../../config.local.json'))
            self.assertIsNone(service.asset('model'))
            service.stop()  # Stopping an idle task must not affect other jobs.


if __name__ == '__main__':
    unittest.main()
