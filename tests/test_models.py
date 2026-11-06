import tempfile
import unittest
from pathlib import Path

import numpy as np

from legolizer.processing3d.volume import VoxelVolume


class VoxelVolumeTests(unittest.TestCase):
    def test_npz_round_trip(self) -> None:
        source = VoxelVolume(
            occupancy=np.ones((2, 3, 4), dtype=bool),
            voxel_size_mm=(8.0, 8.0, 9.6),
            origin_mm=(1.0, 2.0, 3.0),
            semantic="solid",
            source="fixture",
            colors=np.arange(24, dtype=np.int16).reshape(2, 3, 4),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "volume.npz"
            source.save(path)
            loaded = VoxelVolume.load(path)
        np.testing.assert_array_equal(loaded.occupancy, source.occupancy)
        np.testing.assert_array_equal(loaded.colors, source.colors)
        self.assertEqual(loaded.voxel_size_mm, source.voxel_size_mm)
        self.assertEqual(loaded.origin_mm, source.origin_mm)


if __name__ == "__main__":
    unittest.main()
