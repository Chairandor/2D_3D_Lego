"""A short sleeping PowerShell fixture tests timeout ownership, without a solver."""
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.batch import invoke

@unittest.skipUnless(sys.platform=='win32','The production entry point uses Windows PowerShell')
class DeadlineTests(unittest.TestCase):
    def test_deadline_stops_only_the_owned_fixture_child_and_saves_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            (base/'Run.ps1').write_text('param($Config,$Python,$InputModel,$Title)\nStart-Sleep -Seconds 10\n',encoding='utf-8')
            row={'id':'synthetic_deadline','name':'synthetic deadline','input':str(base/'fixture.mpd'),
                 'sha256':'synthetic','status':'running'}
            state={'models':[row]};args=SimpleNamespace(config=base/'config.json',max_model_minutes=.01)
            with patch('lego_workbench.batch.PROJECT',base):
                with self.assertRaises(TimeoutError):invoke(row,args,state,base/'status.json',None)
            diagnostic=json.loads((base/'timeouts/synthetic_deadline.json').read_text(encoding='utf-8'))
            self.assertEqual(diagnostic['pid'],row['child_pid'])
            self.assertEqual(diagnostic['reason'],'USER_MODEL_WALL_TIME_LIMIT')
            self.assertNotEqual(row['exit_code'],0)
            self.assertNotIn('pid',row)

if __name__=='__main__':unittest.main()
