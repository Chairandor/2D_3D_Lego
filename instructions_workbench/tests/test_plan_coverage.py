from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.plan_coverage import validate_nonblank_steps

class StepCoverageTests(unittest.TestCase):
    def test_attachment_is_not_a_blank_instruction(self):
        validate_nonblank_steps({'subassemblies':[{'name':'child','brick_ids':[1]}],
            'steps':[{'brick_ids':[1],'submodel':'child'},{'brick_ids':[],'attaches':'child'}]})
    def test_empty_or_unbuilt_attachment_is_rejected(self):
        for step in ({'brick_ids':[]},{'brick_ids':[],'attaches':'child'}):
            with self.assertRaises(AssertionError):
                validate_nonblank_steps({'subassemblies':[{'name':'child','brick_ids':[1]}],'steps':[step]})
if __name__=='__main__':unittest.main()
