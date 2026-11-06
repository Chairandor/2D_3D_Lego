"""Check LDraw transforms, MPD expansion and incomplete-asset failures."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from ldraw_mesh import load_ldraw


class LDrawMeshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'parts').mkdir()
        (self.root / 'p').mkdir()
        (self.root / 'LDConfig.ldr').write_text(
            '0 !COLOUR Red CODE 4 VALUE #C91A09 EDGE #333333\n'
            '0 !COLOUR Grey CODE 7 VALUE #A0A5A9 EDGE #333333\n', encoding='utf-8')

    def test_mpd_transform_inheritance_and_quad(self) -> None:
        path = self.root / 'test.mpd'
        path.write_text('0 FILE main.ldr\n'
                        '1 4 10 -5 20 1 0 0 0 1 0 0 0 1 sub.ldr\n'
                        '0 FILE sub.ldr\n'
                        '4 16 0 0 0 2 0 0 2 -1 0 0 -1 0\n0 NOFILE\n', encoding='utf-8')
        meshes = load_ldraw(str(path), str(self.root))
        self.assertEqual(len(meshes), 1)
        self.assertEqual(len(meshes[0].faces), 2)
        np.testing.assert_allclose(meshes[0].bounds, [[10, 20, 5], [12, 20, 6]])
        np.testing.assert_array_equal(meshes[0].visual.vertex_colors[0], [201, 26, 9, 255])

    def test_external_part_and_direct_color(self) -> None:
        (self.root / 'parts' / 'part.dat').write_text('3 16 0 0 0 1 0 0 0 -1 0\n', encoding='utf-8')
        path = self.root / 'test.ldr'
        path.write_text('1 0x2123456 0 0 0 1 0 0 0 1 0 0 0 1 part.dat\n', encoding='utf-8')
        mesh = load_ldraw(str(path), str(self.root))[0]
        np.testing.assert_array_equal(mesh.visual.vertex_colors[0], [18, 52, 86, 255])
        self.assertEqual(len(mesh.faces), 1)

    def test_missing_reference_and_cycle_fail(self) -> None:
        path = self.root / 'test.ldr'
        path.write_text('1 4 0 0 0 1 0 0 0 1 0 0 0 1 missing.dat\n', encoding='utf-8')
        with self.assertRaises(FileNotFoundError):
            load_ldraw(str(path), str(self.root))
        path.write_text('1 4 0 0 0 1 0 0 0 1 0 0 0 1 test.ldr\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '循环引用'):
            load_ldraw(str(path), str(self.root))


if __name__ == '__main__':
    unittest.main()
