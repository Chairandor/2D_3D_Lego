from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.batch import graphics_first

class GraphicsPriorityTests(unittest.TestCase):
    def test_saved_complete_plan_goes_directly_to_draft(self):
        row={};plan=Path('saved_complete_plan')
        with patch('lego_workbench.batch.plan_checkpoint',return_value=plan),patch('lego_workbench.batch.invoke',return_value='result') as call:
            self.assertEqual(graphics_first(row,SimpleNamespace(),{},Path('state.json'),None),'result')
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs,{'mode':'export-draft','plan_run':plan})
        self.assertEqual(row['step_verification_status'],'INCOMPLETE')
    def test_prepare_timeout_with_saved_plan_still_exports_graphics(self):
        plan=Path('saved_just_before_timeout');row={}
        with patch('lego_workbench.batch.plan_checkpoint',side_effect=[None,plan]),patch('lego_workbench.batch.invoke',side_effect=[TimeoutError(),'draft_result']) as call:
            self.assertEqual(graphics_first(row,SimpleNamespace(),{},Path('state.json'),None),'draft_result')
        self.assertEqual([c.kwargs['mode'] for c in call.call_args_list],['prepare','export-draft'])
        self.assertIn('previous_verification_error',row)
if __name__=='__main__':unittest.main()
