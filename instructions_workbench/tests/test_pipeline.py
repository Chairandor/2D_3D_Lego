"""Real 0.6.0 physics on THREE synthetic bricks; native process is simulated.

Does NOT read or solve bunny/chicken/MPD. The mock PNGs are explicitly labelled
test fixtures and never included in any project or model delivery ZIP.
Run: python -m unittest discover -s tests -v
"""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lego_workbench.config import Settings
from lego_workbench.environment import ensure_palette
from lego_workbench.compat.api import load_api
from lego_workbench.engine import checked_run, find_existing, generate_or_reuse, instruction_config
from lego_workbench.storage import CORE_FILES, digest, fingerprint, model_lock, read_json, write_json
from lego_workbench.rendering import pipeline
from lego_workbench.rendering.framing import FramedRunner

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'synthetic.ldr'
        cls.source.write_text('0 SYNTHETIC TEST - not a project model\n' + '\n'.join(
            f'1 15 -10 {-24*i} -10 1 0 0 0 1 0 0 0 1 3001.dat' for i in range(1,4)) + '\n')
        cls.library = cls.root / 'ldraw'; (cls.library / 'parts').mkdir(parents=True)
        (cls.library / 'LDConfig.ldr').write_text('0 !COLOUR White CODE 15 VALUE #FFFFFF EDGE #333333\n')
        (cls.library / 'parts' / '3001.dat').write_text('0 SYNTHETIC bounding box fixture\n4 16 -40 -24 -20 40 -24 -20 40 0 20 -40 0 20\n')
        cls.renderer = cls.root / 'LeoCAD.exe'; cls.renderer.write_text('mock process only')
        cls.settings = Settings(cls.root / 'output', (), cls.renderer, cls.library, True, 7, 'warn', {})
        cls.api = load_api(); ensure_palette(cls.library)
        cls.stdout = io.StringIO()
        with redirect_stdout(cls.stdout):
            cls.run_folder, reused = generate_or_reuse(cls.api, cls.settings, cls.source, 'synthetic', '合成测试')
        cls.saved = {n: digest(cls.run_folder / n) for n in CORE_FILES}
        cls.input_hash = digest(cls.source)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_01_real_import_physics_and_step_coverage(self):
        report, plan, bom = checked_run(self.run_folder, self.source)
        self.assertEqual(report['brick_count'], 3)
        self.assertEqual(bom['brick_count'], 3)
        self.assertEqual(len(set(i for s in plan['steps'] for i in s['brick_ids'])), 3)
        self.assertTrue((self.run_folder / 'FINAL_READY.json').is_file())
        self.assertTrue((self.run_folder / 'PLAN_READY.json').is_file())
        self.assertTrue((self.run_folder / 'VERIFY_READY.json').is_file())
        self.assertEqual(read_json(self.run_folder / 'progress.json')['status'], 'completed')
        self.assertEqual(digest(self.source), self.input_hash)

    def test_02_repeat_never_imports_plans_or_solves(self):
        forbidden = dict(self.api)
        def fail(*a, **kw):
            raise AssertionError('completed model must not be solved again')
        for key in ('import_ldraw', 'analyze', 'plan_instructions', 'certify_instructions'):
            forbidden[key] = fail
        with redirect_stdout(self.stdout):
            run, reused = generate_or_reuse(forbidden, self.settings, self.source, 'synthetic', '合成测试')
        self.assertTrue(reused); self.assertEqual(run, self.run_folder)

    def test_03_verify_interruption_reuses_final_and_plan(self):
        root = self.root / 'resume_case'; root.mkdir()
        destination = root / self.run_folder.name
        shutil.copytree(self.run_folder, destination)
        for n in ('CORE_READY.json', 'READY_FOR_RENDER', 'report.json', 'VERIFY_READY.json', 'certification.json'):
            (destination / n).unlink(missing_ok=True)
        api = dict(self.api)
        def fail(*a, **kw):
            raise AssertionError('saved final and plan must not be recomputed')
        api['analyze'] = fail; api['plan_instructions'] = fail
        settings = replace(self.settings, output_root=root)
        with redirect_stdout(self.stdout):
            run, reused = generate_or_reuse(api, settings, self.source, 'synthetic', '合成测试')
        self.assertFalse(reused); self.assertTrue((run / 'VERIFY_READY.json').is_file())
        self.assertEqual(digest(run / 'plan.json'), self.saved['plan.json'])

    def test_04_legacy_fallback_and_config_invalidation(self):
        root = self.root / 'legacy'; root.mkdir()
        valid = root / 'valid'; corrupt = root / 'corrupt'
        shutil.copytree(self.run_folder, valid); shutil.copytree(self.run_folder, corrupt)
        for folder in (valid, corrupt):
            (folder / 'CORE_READY.json').unlink()
        (corrupt / 'plan.json').write_text('{}')
        settings = replace(self.settings, output_root=root / 'new', reuse_roots=(root,))
        config = instruction_config(self.api, settings)
        with redirect_stdout(self.stdout):
            found = find_existing(settings, self.source, config, root / 'nonexistent')
        self.assertEqual(found, valid)
        changed = replace(settings, step_size=5)
        self.assertIsNone(find_existing(changed, self.source, instruction_config(self.api, changed), root / 'nonexistent'))

    def test_05_same_model_lock_blocks_second_instance(self):
        with model_lock(self.settings.output_root, self.input_hash):
            with self.assertRaises(RuntimeError):
                with model_lock(self.settings.output_root, self.input_hash):
                    pass

    def test_06_native_render_export_resume_and_core_preservation(self):
        from PIL import Image, ImageDraw
        calls = []
        def fake_native(cmd, **kwargs):
            calls.append(cmd)
            self.assertIn('--camera-position-ldraw', cmd)
            first, last = int(cmd[cmd.index('-f')+1]), int(cmd[cmd.index('-t')+1])
            dest = Path(cmd[cmd.index('-i')+1])
            w,h = int(cmd[cmd.index('-w')+1]), int(cmd[cmd.index('-h')+1])
            for i in range(first, last+1):
                p = dest if first == last else dest.with_name(f'frame{i:02d}.png')
                image = Image.new('RGB', (w,h), 'white'); draw = ImageDraw.Draw(image)
                draw.rectangle((w//5,h//5,4*w//5,4*h//5), fill='#b8c7cd', outline='#e7bd3f', width=4)
                draw.text((w//4,h//3),'SYNTHETIC PIPELINE TEST',fill='black')
                image.save(p)
            return subprocess.CompletedProcess(cmd,0,'','')
        def runner(library, cache):
            return FramedRunner(library, cache, executor=fake_native)
        args = type('Args', (), {'renderer':self.renderer, 'ldraw_dir':self.library,
                                'title':'合成测试', 'expected_sha256':self.input_hash})()
        with patch.object(pipeline, 'FramedRunner', runner), redirect_stdout(self.stdout):
            out = pipeline.render_and_booklet(args,'synthetic',self.run_folder,self.source)
            count = len(calls)
            pipeline.render_and_booklet(args,'synthetic',self.run_folder,self.source)
            self.assertEqual(len(calls),count)
            next((out / 'details').glob('*_opposite.png')).unlink()
            pipeline.render_and_booklet(args,'synthetic',self.run_folder,self.source)
            self.assertEqual(len(calls),count)  # native cache restores missing PNG
        self.assertEqual({n:digest(self.run_folder/n) for n in CORE_FILES}, self.saved)
        self.assertEqual(digest(self.source),self.input_hash)
        self.assertTrue((out/'instructions_cn.pdf').is_file())
        self.assertTrue((out/'instructions_cn.html').is_file())
        # Retain the QA specimen only when a caller explicitly requests it.
        import os
        if os.environ.get('LEGO_TEST_QA_DIR'):
            target=Path(os.environ['LEGO_TEST_QA_DIR']);target.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(out/'instructions_cn.pdf',target/'synthetic_pipeline.pdf')

    def test_07_cli_delivery_bundle_and_json_summary(self):
        from lego_workbench.cli import main
        source = self.root / '合成测试.ldr'
        shutil.copyfile(self.source, source)
        config = self.root / 'cli.json'
        write_json(config, {'renderer':str(self.renderer), 'ldraw_dir':str(self.library),
                            'output_root':str(self.settings.output_root), 'reuse_roots':[],
                            'ground':True, 'step_size':7, 'stability_policy':'warn', 'models':{}})
        forbidden = dict(self.api)
        def fail(*a, **kw):
            raise AssertionError('CLI must reuse the complete synthetic result')
        for key in ('import_ldraw','analyze','plan_instructions','certify_instructions'):
            forbidden[key] = fail
        with patch('lego_workbench.environment.check', return_value=forbidden), redirect_stdout(self.stdout):
            result = main(['--config',str(config),'--input',str(source),'--mode','legacy-full'])
        self.assertIn(result, (0,2))
        latest = read_json(self.settings.output_root/'LATEST.json')
        self.assertEqual(latest['failed'],[])
        self.assertTrue(latest['completed'][0]['analysis_and_plan_reused'])
        import zipfile
        archive = Path(latest['completed'][0]['team_delivery'])
        with zipfile.ZipFile(archive) as z:
            names = set(z.namelist())
            self.assertIn('stability_report.html',names)
            self.assertIn('bom.csv',names)
            self.assertIn('analysis_and_steps/model_steps.mpd',names)
            self.assertIn('booklet/instructions_cn.pdf',names)
            self.assertIn('booklet/instructions_cn.html',names)
            self.assertTrue(any(n.startswith('booklet/steps/') for n in names))

    def test_08_explicit_draft_never_plans_solves_or_forges_verification(self):
        from lego_workbench.draft import export_draft,checked_draft
        from PIL import Image,ImageDraw
        origin=self.root/'draft_origin';shutil.copytree(self.run_folder,origin)
        for n in ('report.json','CORE_READY.json','VERIFY_READY.json','READY_FOR_RENDER','certification.json'):
            (origin/n).unlink(missing_ok=True)
        saved={p.name:digest(p) for p in origin.iterdir() if p.is_file()}
        def fake_native(cmd,**kwargs):
            first,last=int(cmd[cmd.index('-f')+1]),int(cmd[cmd.index('-t')+1]);dest=Path(cmd[cmd.index('-i')+1])
            w,h=int(cmd[cmd.index('-w')+1]),int(cmd[cmd.index('-h')+1])
            for i in range(first,last+1):
                path=dest if first==last else dest.with_name(f'frame{i:02d}.png')
                im=Image.new('RGB',(w,h),'white');draw=ImageDraw.Draw(im)
                draw.rectangle((w//5,h//5,4*w//5,4*h//5),fill='#b8c7cd')
                draw.text((w//4,h//3),'SYNTHETIC DRAFT TEST',fill='black');im.save(path)
            return subprocess.CompletedProcess(cmd,0,'','')
        def runner(library,cache):return FramedRunner(library,cache,executor=fake_native)
        settings=replace(self.settings,output_root=self.root/'draft_output')
        with patch('lego_workbench.compat.api.load_api',side_effect=AssertionError('No import/planning/solver API allowed')), patch.object(pipeline,'FramedRunner',runner),redirect_stdout(self.stdout):
            result=export_draft(settings,self.source,origin,'合成草稿')
            with patch.object(pipeline,'render_and_booklet',side_effect=AssertionError('completed draft must not render again')):
                self.assertEqual(export_draft(settings,self.source,origin,'合成草稿'),result)
        summary=read_json(Path(result['files']['stability_summary']))
        draft_run=Path(summary['source_run']);checked_draft(draft_run,self.source)
        self.assertEqual(summary['step_verification_status'],'INCOMPLETE')
        self.assertIsNone(summary['certification']);self.assertFalse(result['instruction_certified'])
        self.assertIn('未完成验证',Path(result['files']['html']).read_text(encoding='utf-8'))
        self.assertEqual(saved,{p.name:digest(p) for p in origin.iterdir() if p.is_file()})
        for n in ('report.json','CORE_READY.json','VERIFY_READY.json','READY_FOR_RENDER'):
            self.assertFalse((draft_run/n).exists())
        from lego_workbench.batch import result_for
        self.assertIsNotNone(result_for({'sha256':self.input_hash},settings))

    def test_09_prepare_reuses_plan_without_certification(self):
        root=self.root/'prepare_only';root.mkdir();origin=root/self.run_folder.name
        shutil.copytree(self.run_folder,origin)
        for n in ('report.json','CORE_READY.json','VERIFY_READY.json','READY_FOR_RENDER','certification.json'):
            (origin/n).unlink(missing_ok=True)
        api=dict(self.api)
        for key in ('analyze','plan_instructions','certify_instructions'):
            api[key]=lambda *a,**k:(_ for _ in ()).throw(AssertionError('saved plan must be reused'))
        with redirect_stdout(self.stdout):
            run,_=generate_or_reuse(api,replace(self.settings,output_root=root),self.source,'synthetic','合成准备',prepare_only=True)
        self.assertTrue((run/'PLAN_READY.json').is_file());self.assertFalse((run/'VERIFY_READY.json').exists())

    def test_10_standalone_verification_preserves_existing_booklet_and_core(self):
        from lego_workbench.verification import verify_saved_plan
        with redirect_stdout(self.stdout):
            result=verify_saved_plan(self.settings,self.source,self.run_folder,max_seconds=30)
        self.assertEqual(result['status'],'COMPLETED')
        self.assertEqual(result['input_sha256'],self.input_hash)
        self.assertIsNotNone(result['certification'])
        self.assertEqual({n:digest(self.run_folder/n) for n in CORE_FILES},self.saved)
        with patch('lego_workbench.verification.multiprocessing.get_context',side_effect=AssertionError('must reuse completed verification')):
            self.assertEqual(verify_saved_plan(self.settings,self.source,self.run_folder)['status'],'COMPLETED')

if __name__ == '__main__':
    unittest.main(verbosity=2)
