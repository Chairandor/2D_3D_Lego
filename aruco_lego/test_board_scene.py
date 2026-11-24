import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pyrender
import trimesh

from aruco_lego_ar import load_lego_meshes
from board_scene import BoardScene


class BoardSceneTests(unittest.TestCase):
    def test_ldraw_real_size_and_independent_board_positions(self):
        raw = trimesh.creation.box(extents=[160, 80, 24])
        with patch('aruco_lego_ar.load_ldraw', return_value=[raw]):
            (mesh,) = load_lego_meshes('sample.mpd', 'z', None)
        np.testing.assert_allclose(mesh.bounds, [[-.032, -.016, 0], [.032, .016, .0096]])
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'control.json'
            control.write_text(json.dumps({'placements': [
                {'id': 'one', 'path': 'one.mpd', 'x_cm': -3, 'y_cm': 5, 'angle': 90, 'label': 'One'},
                {'id': 'two', 'path': 'one.mpd', 'x_cm': 3, 'y_cm': 5, 'angle': 0, 'label': 'Two'},
            ]}), encoding='utf-8')
            scene = pyrender.Scene()
            board = BoardScene(scene, control, lambda _: [mesh])
            board.update()
            board.update()
            self.assertEqual(len(scene.mesh_nodes), 2)
            self.assertFalse(board.errors)
            marker = np.eye(4)
            marker[:3, 3] = [.11, .07, -.5]
            board.set_pose(marker)
            first = scene.get_pose(board.nodes['one'][0])
            second = scene.get_pose(board.nodes['two'][0])
            np.testing.assert_allclose(first[:3, 3], [.08, .12, -.5])
            np.testing.assert_allclose(second[:3, 3], [.14, .12, -.5])
            np.testing.assert_allclose(first[:3, 0], [0, 1, 0], atol=1e-10)


if __name__ == '__main__':
    unittest.main()
