"""食指姿态、保持触发与刚体变换回归；不代替真人识别验收。"""
import unittest

import numpy as np

from gesture_control import ModelRotation, PointingRecognizer, pointing_direction


def hand_fixture(angle=0):
    points = np.zeros((21, 3))
    points[0] = [0, 90, 0]
    points[5:9] = [[-25, 20, 0], [-25, -10, 0], [-25, -30, 0], [-25, -55, 0]]
    for mcp, x, y in [(9, 0, 15), (13, 20, 20), (17, 38, 30)]:
        points[mcp:mcp+4] = [[x, y, 0], [x, y-20, 0], [x+5, y+10, 0], [x, y+30, 0]]
    c, s = np.cos(angle), np.sin(angle)
    points[:, :2] = points[:, :2] @ np.array([[c, -s], [s, c]]).T + [320, 240]
    return points / [640, 480, 640]


class PointingTests(unittest.TestCase):
    def test_four_screen_directions(self):
        for angle, direction in [(0, 'up'), (np.pi/2, 'right'), (np.pi, 'down'), (-np.pi/2, 'left')]:
            self.assertEqual(pointing_direction(hand_fixture(angle), 640, 480), direction)

    def test_fist_v_sign_open_hand_diagonal_and_invalid_landmarks_rejected(self):
        self.assertIsNone(pointing_direction(np.zeros((21, 3)), 640, 480))
        points = hand_fixture()
        points[12] = points[9] + [0, -0.15, 0]  # Extended middle finger: V sign.
        self.assertIsNone(pointing_direction(points, 640, 480))
        for mcp in (13, 17):
            points[mcp+3] = points[mcp] + [0, -0.15, 0]
        self.assertIsNone(pointing_direction(points, 640, 480))
        self.assertIsNone(pointing_direction(hand_fixture(np.pi/4), 640, 480))
        self.assertIsNone(pointing_direction(np.full((21, 3), np.nan), 640, 480))

    def test_static_point_continues_after_confirmation(self):
        for direction in ('left', 'right', 'up', 'down'):
            recognizer = PointingRecognizer()
            events = [recognizer.update({'hand': direction}, i/20) for i in range(41)]
            self.assertTrue(all(event is None for event in events[:7]))
            self.assertTrue(all(event == direction for event in events[8:]))
            self.assertEqual(recognizer.state, 'active')

    def test_release_and_change_direction_stop_then_confirm_again(self):
        recognizer = PointingRecognizer()
        for i in range(14):
            event = recognizer.update({'hand':'left'}, i/20)
        self.assertEqual(event, 'left')
        self.assertIsNone(recognizer.update({}, 0.7))
        self.assertEqual(recognizer.state, 'idle')
        self.assertIsNone(recognizer.update({'hand':'left'}, 1))
        self.assertIsNone(recognizer.update({'hand':'left'}, 1.2))
        self.assertEqual(recognizer.update({'hand':'left'}, 1.4), 'left')
        self.assertIsNone(recognizer.update({'hand':'right'}, 1.5))
        self.assertIsNone(recognizer.update({'hand':'right'}, 1.7))
        self.assertEqual(recognizer.update({'hand':'right'}, 1.9), 'right')

    def test_large_model_low_frame_rate_still_accepts_a_held_direction(self):
        recognizer = PointingRecognizer()
        events = [recognizer.update({'hand':'left'}, i * 0.5) for i in range(6)]
        self.assertEqual(events, [None, None, 'left', 'left', 'left', 'left'])

    def test_tracking_loss_and_conflict_stop_active_direction(self):
        for observations in ({}, {'a':'left', 'b':'right'}):
            recognizer = PointingRecognizer()
            for timestamp in (0, 0.2, 0.4):
                event = recognizer.update({'a':'left'}, timestamp)
            self.assertEqual(event, 'left')
            self.assertIsNone(recognizer.update(observations, 0.5))
            self.assertIsNone(recognizer.update({'a':'left'}, 0.6))

    def test_flicker_and_conflicting_hands_do_not_trigger(self):
        recognizer = PointingRecognizer()
        for i in range(50):
            self.assertIsNone(recognizer.update({'a':'left','b':'right'}, i/20))
        self.assertEqual(recognizer.state, 'ambiguous')
        recognizer.reset()
        for i in range(20):
            self.assertIsNone(recognizer.update({'hand':'left' if i%2 else 'up'}, i/20))


class RotationTests(unittest.TestCase):
    def test_model_center_is_fixed_during_combined_rotations(self):
        center = np.array([0.02, -0.01, 0.05])
        model = ModelRotation(center)
        for i, direction in enumerate(('left', 'up', 'right', 'down')):
            model.drive(direction, i)
            for dt in (0, 0.1, 0.3):
                transform = model.advance(i + dt)
                np.testing.assert_allclose((transform @ np.r_[center, 1])[:3], center, atol=1e-10)
                np.testing.assert_allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-10)
                self.assertAlmostEqual(np.linalg.det(transform[:3, :3]), 1)

    def test_direction_signs_and_reset(self):
        for direction, axis, sign in [('left', 1, -1), ('right', 1, 1), ('up', 1, 1), ('down', 1, -1)]:
            model = ModelRotation(np.zeros(3), 90)
            model.drive(direction, 1)
            for timestamp in np.linspace(1.1, 2, 10):
                transform = model.advance(timestamp)
            point = np.array([1, 0, 0]) if direction in ('left', 'right') else np.array([0, 0, 1])
            self.assertAlmostEqual((transform[:3, :3] @ point)[axis], sign)
            model.reset()
            np.testing.assert_allclose(model.advance(3), np.eye(4))

    def test_invalid_rotation_parameters(self):
        for angle in (0, float('nan'), 181):
            with self.assertRaises(ValueError):
                ModelRotation(np.zeros(3), angle)

    def test_held_direction_keeps_rotating_until_release(self):
        model = ModelRotation(np.zeros(3), 60)
        self.assertTrue(model.drive('right', 0))
        for timestamp in np.linspace(0.1, 2, 20):
            self.assertFalse(model.drive('right', timestamp))
        self.assertAlmostEqual(model.rotation[0, 0], np.cos(np.radians(120)))
        self.assertEqual(model.sequence, 1)
        model.drive(None, 2)
        stopped = model.advance(2).copy()
        np.testing.assert_allclose(model.advance(20), stopped)
        self.assertFalse(model.status()['is_rotating'])

    def test_elapsed_time_controls_speed_independent_of_frame_rate(self):
        matrices = []
        for frames in (10, 40):
            model = ModelRotation(np.zeros(3), 60)
            model.drive('up', 0)
            for timestamp in np.linspace(2/frames, 2, frames):
                model.drive('up', timestamp)
            matrices.append(model.rotation.copy())
        np.testing.assert_allclose(matrices[0], matrices[1], atol=1e-10)

    def test_stall_does_not_cause_large_rotation_jump(self):
        model = ModelRotation(np.zeros(3), 60)
        model.drive('right', 0)
        model.advance(10)
        self.assertAlmostEqual(model.rotation[0, 0], np.cos(np.radians(30)))

if __name__ == '__main__':
    unittest.main()
