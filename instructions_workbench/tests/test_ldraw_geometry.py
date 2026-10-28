from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.ldraw_geometry import compare

class GeometryTests(unittest.TestCase):
    def test_nested_rotation_colour_and_translation_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.mpd';export=Path(temp)/'steps.mpd'
            source.write_text('0 FILE root.mpd\n1 4 100 0 0 0 0 -1 0 1 0 1 0 0 child.ldr\n0 FILE child.ldr\n1 16 20 -8 0 1 0 0 0 1 0 0 0 1 3024.dat\n')
            export.write_text('0 FILE output.mpd\n1 4 100 -8 20 0 0 -1 0 1 0 1 0 0 3024.dat\n0 STEP\n')
            self.assertEqual(compare(source,export)['source_records'],1)
            (Path(temp)/'child.ldr').write_text('1 16 20 -8 0 1 0 0 0 1 0 0 0 1 3024.dat\n')
            source.write_text('1 4 100 0 0 0 0 -1 0 1 0 1 0 0 child.ldr\n')
            self.assertEqual(compare(source,export)['source_records'],1)
            export.write_text(export.read_text().replace('100 -8 20','100 -8 21'))
            with self.assertRaises(AssertionError):compare(source,export)

if __name__=='__main__':unittest.main()
