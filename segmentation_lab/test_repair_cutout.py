"""Small shape-preservation checks for automatic cutout cleanup."""

import unittest

import numpy as np

from segmentation_lab.repair_cutout import remove_negligible_islands


class CutoutRepairTests(unittest.TestCase):
    def test_removes_dust_without_rounding_main_shape(self):
        mask = np.zeros((160, 160), dtype=bool)
        mask[20:130, 20:130] = True
        mask[145:147, 145:147] = True
        result, report = remove_negligible_islands(mask)
        self.assertTrue(np.array_equal(result[20:130, 20:130], mask[20:130, 20:130]))
        self.assertFalse(result[145:147, 145:147].any())
        self.assertEqual(report["removed_components"], 1)

    def test_keeps_substantial_detached_part(self):
        mask = np.zeros((160, 160), dtype=bool)
        mask[20:130, 20:130] = True
        mask[140:150, 140:150] = True
        result, report = remove_negligible_islands(mask)
        self.assertTrue(np.array_equal(result, mask))
        self.assertEqual(report["retained_secondary_components"], 1)


if __name__ == "__main__":
    unittest.main()
