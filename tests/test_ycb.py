import unittest
from io import BytesIO
from pathlib import Path

import h5py
import numpy as np
from PIL import Image

from legolizer.processing3d.ycb import (
    _object_foreground,
    _read_calibration,
    register_depth_to_rgb,
)
from legolizer.processing3d.rgbd import _intrinsic


class YCBAdapterTests(unittest.TestCase):
    def test_identity_registration(self) -> None:
        depth = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float)
        intrinsic = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        )
        registered = register_depth_to_rgb(
            depth, intrinsic, intrinsic, np.eye(4), rgb_shape=(2, 2)
        )
        np.testing.assert_allclose(registered, depth)

    def test_foreground_uses_minority_value(self) -> None:
        mask = np.ones((4, 4), dtype=np.uint8) * 255
        mask[1:3, 1:3] = 0
        foreground = _object_foreground(Image.fromarray(mask).convert("1"))
        self.assertEqual(int(foreground.sum()), 4)
        self.assertTrue(foreground[1, 1])

    def test_calibration_prefers_native_depth_intrinsic(self) -> None:
        payload = BytesIO()
        with h5py.File(payload, "w") as calibration:
            calibration["NP5_depth_K"] = np.eye(3) * 2
            calibration["NP5_ir_K"] = np.eye(3) * 4
            calibration["NP5_rgb_K"] = np.eye(3)
            calibration["NP5_ir_depth_scale"] = np.array([1.0])
            calibration["H_NP5_from_NP5"] = np.eye(4)
            calibration["H_NP5_ir_from_NP5"] = np.eye(4)

        result = _read_calibration(payload.getvalue(), "NP5", "NP5")

        self.assertEqual(result["depth_intrinsic_key"], "NP5_depth_K")
        np.testing.assert_array_equal(result["depth_intrinsic"], np.eye(3) * 2)

    def test_frame_intrinsic_overrides_manifest_default(self) -> None:
        default = {
            "width": 640,
            "height": 480,
            "fx": 500.0,
            "fy": 500.0,
            "cx": 320.0,
            "cy": 240.0,
        }
        override = {**default, "fx": 700.0, "cx": 300.0}

        intrinsic = _intrinsic(
            {"intrinsic": default}, Path("."), {"intrinsic": override}
        )

        matrix = intrinsic.intrinsic_matrix
        self.assertEqual(matrix[0, 0], 700.0)
        self.assertEqual(matrix[0, 2], 300.0)


if __name__ == "__main__":
    unittest.main()
