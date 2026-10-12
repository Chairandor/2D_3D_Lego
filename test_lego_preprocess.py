"""Synthetic regressions for LEGO-oriented foreground fusion."""

import unittest

import numpy as np

from lego_preprocess import extract_mask


class ForegroundTests(unittest.TestCase):
    def test_rescues_appendage_and_rejects_external_shadow(self) -> None:
        image = np.full((200, 200, 3), 150, dtype=np.uint8)
        image[40:125, 40:125] = [245, 245, 245]  # bright subject
        image[125:165, 70:85] = [170, 125, 85]  # missed coloured appendage
        image[78:165, 128:180] = [75, 75, 75]  # cast shadow
        image[65:74, 65:74] = [20, 20, 20]  # dark detail within subject
        model = np.zeros((200, 200), dtype=np.uint8)
        model[40:125, 40:125] = 255
        model[78:165, 128:180] = 255

        mask, report = extract_mask(image, model)

        self.assertEqual(report["method"], "model_plus_uniform_background")
        self.assertTrue(mask[55, 55])
        self.assertTrue(mask[145, 75])
        self.assertTrue(mask[69, 69])
        self.assertFalse(mask[130, 155])

    def test_complex_border_defers_to_model(self) -> None:
        image = np.zeros((80, 80, 3), dtype=np.uint8)
        image[:, :40] = [0, 200, 0]
        image[:, 40:] = [200, 0, 200]
        model = np.zeros((80, 80), dtype=np.uint8)
        model[20:60, 20:60] = 255

        mask, report = extract_mask(image, model)

        self.assertEqual(report["method"], "model")
        np.testing.assert_array_equal(mask, model > 0)


if __name__ == "__main__":
    unittest.main()
