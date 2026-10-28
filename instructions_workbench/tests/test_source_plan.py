"""Source STEP adaptation preserves child boundaries without planning."""
from pathlib import Path
from types import SimpleNamespace as S
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.source_plan import source_groups

class SourceStepTests(unittest.TestCase):
    def test_child_steps_and_repeated_instances_are_kept_in_occurrence_order(self):
        def ref(global_step,local_step,instance=None):
            path=(() if instance is None else (S(source_model='root.mpd',reference='child.ldr',source_line=instance),))
            return S(global_step=global_step,source_step=local_step,path=(*path,S(source_model='child.ldr',reference='3001.dat',source_line=local_step)))
        refs={0:ref(1,1),1:ref(2,1,20),2:ref(2,1,20),3:ref(2,2,20),4:ref(2,1,25),5:ref(3,3)}
        groups=source_groups(refs)
        self.assertEqual([g['brick_ids'] for g in groups],[[0],[1,2],[3],[4],[5]])
        self.assertEqual([i for g in groups for i in g['brick_ids']],list(refs))
if __name__=='__main__':unittest.main()
