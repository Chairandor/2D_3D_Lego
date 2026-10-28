"""Batch safety checks use tiny files only; never run real model solvers."""
from pathlib import Path
import json
import hashlib
from contextlib import redirect_stdout
import io
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.batch import catalog, result_for, EXCLUDED_HASH, main, restore_packaged_checkpoint

class BatchTests(unittest.TestCase):
    def test_portable_checkpoint_restores_without_overwriting_local_progress(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root); checkpoint=base/'partial';checkpoint.mkdir()
            identity={'source_sha256':'a'*64,'ground':True,'fingerprint':'b'*64,
                      'config':{'target_step_size':7,'stability_policy':'warn'}}
            (checkpoint/'run_identity.json').write_text(json.dumps(identity))
            (checkpoint/'plan.json').write_text('saved plan')
            settings=type('Settings',(),{'output_root':base/'out','ground':True,'step_size':7,'policy':'warn'})()
            row={'name':'synthetic','sha256':'a'*64,'resume_checkpoint':str(checkpoint)}
            with redirect_stdout(io.StringIO()):restore_packaged_checkpoint(row,settings)
            target=base/'out'/('custom_'+'a'*12+'_'+'b'*12)
            self.assertEqual((target/'plan.json').read_text(),'saved plan')
            (target/'plan.json').write_text('newer local progress')
            restore_packaged_checkpoint(row,settings)
            self.assertEqual((target/'plan.json').read_text(),'newer local progress')
            with self.assertRaises(ValueError):
                restore_packaged_checkpoint(dict(row,sha256='wrong input'),settings)

    def test_inventory_does_not_reset_completed_state(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root); source=base/'model.mpd'; source.write_text('synthetic inventory only')
            h=hashlib.sha256(source.read_bytes()).hexdigest()
            manifest=base/'manifest.json'; manifest.write_text(json.dumps({'models':[{'id':'new','input':str(source)}]}))
            config=base/'config.json'; config.write_text(json.dumps({'models':{},'output_root':str(base/'out'),'renderer':'unused','ldraw_dir':'unused'}))
            state=base/'state.json'; state.write_text(json.dumps({'models':[{'id':'new','sha256':h,'status':'complete','result_path':'accepted.json'}],'finished':True}))
            before=state.read_bytes()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--manifest',str(manifest),'--config',str(config),'--state',str(state),'--inventory-only']),0)
            self.assertEqual(state.read_bytes(),before)
            inventory=json.loads((base/'batch_inventory.json').read_text(encoding='utf-8'))
            self.assertEqual(inventory['models'][0]['status'],'complete')

    def test_dedup_and_old_hash_exclusion_do_not_exclude_all_model_mpd_names(self):
        with tempfile.TemporaryDirectory() as root:
            base=Path(root); files=[]
            for name,content in [('Apple','new-a'),('Fox','new-a'),('Orange','new-b'),('old','old')]:
                p=base/name/'model.mpd'; p.parent.mkdir(); p.write_text(content); files.append(p)
            def hashed(p): return EXCLUDED_HASH if p.parent.name=='old' else p.read_text()
            with patch('lego_workbench.batch.digest',side_effect=hashed):
                rows,excluded=catalog({'models':[{'id':p.parent.name,'input':str(p)} for p in files]})
            self.assertEqual([r['id'] for r in rows],['Apple','Orange'])
            self.assertEqual(len(excluded),2)

    def test_accepted_model_missing_result_stops_without_starting_pipeline(self):
        with tempfile.TemporaryDirectory() as root:
            settings=type('Settings',(),{'output_root':Path(root)})()
            row={'id':'Fox_08stud','sha256':'x','reuse_only':True,'completed_result':str(Path(root)/'missing.json')}
            with patch('lego_workbench.batch.subprocess.Popen',side_effect=AssertionError('must never start')):
                with self.assertRaises(FileNotFoundError): result_for(row,settings)

    def test_completed_result_can_be_selected_without_any_solver_import(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'result.json'
            path.write_text(json.dumps({'input_sha256':'x','booklet_complete':True,'files':{'stability_summary':'summary.json'}}))
            (Path(root)/'summary.json').write_text(json.dumps({'source_run':'.'}))
            (Path(root)/'report.json').write_text(json.dumps({'ground':True,'package_version':'0.6.0',
                'instructions_config':{'target_step_size':7,'stability_policy':'warn'}}))
            settings=type('Settings',(),{'output_root':Path(root),'ground':True,'step_size':7,'policy':'warn'})()
            self.assertEqual(result_for({'completed_result':str(path),'sha256':'x'},settings),path)
            changed=type('Settings',(),{'output_root':Path(root),'ground':True,'step_size':9,'policy':'warn'})()
            self.assertIsNone(result_for({'completed_result':str(path),'sha256':'x'},changed))

if __name__=='__main__': unittest.main()
