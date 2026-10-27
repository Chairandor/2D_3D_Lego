"""New orchestration tests use synthetic bricks/owned child processes only."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import multiprocessing as mp
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lego_workbench.config import Settings
from lego_workbench.storage import digest,read_json,write_json
from lego_workbench.bounded_analysis import run as analyze

def phases(source,run,out,library,ground):
    out=Path(out)
    time.sleep(.4) # Represents final-model work, under the SAME clock as steps.
    write_json(out/'phase_one.json',{'complete':True})
    time.sleep(10)

def failure(source,run,out,library,ground):
    write_json(Path(out)/'worker_result.json',{'status':'FAILED','error':'synthetic physical failure'})

def exception(source,run,out,library,ground):
    raise RuntimeError('synthetic crash, no valid solver result')

class InstructionsFirstTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'fixture.ldr'
        self.source.write_text('0 SYNTHETIC\n'+'\n'.join(f'1 15 -10 {-24*i} -10 1 0 0 0 1 0 0 0 1 3001.dat' for i in range(1,4))+'\n')
        lib=self.root/'ldraw';(lib/'parts').mkdir(parents=True)
        (lib/'LDConfig.ldr').write_text('0 !COLOUR White CODE 15 VALUE #FFFFFF EDGE #333333\n')
        (lib/'parts/3001.dat').write_text('0 SYNTHETIC\n4 16 -40 -24 -20 40 -24 -20 40 0 20 -40 0 20\n')
        renderer=self.root/'LeoCAD.exe';renderer.touch()
        self.settings=Settings(self.root/'output',(),renderer,lib,True,1,'warn',{})
    def tearDown(self):self.temp.cleanup()

    def test_planning_never_calls_static_api_and_preserves_exact_geometry(self):
        from lego_workbench.compat.api import load_api
        from lego_workbench.planning import prepare
        from lego_workbench.ldraw_geometry import compare
        api=load_api()
        for key in ('analyze','plan_instructions','certify_instructions'):
            api[key]=lambda *a,**k:(_ for _ in ()).throw(AssertionError('physics in first phase'))
        with patch('lego_workbench.compat.api.load_api',return_value=api):
            run=prepare(self.settings,self.source)
        plan=read_json(run/'plan.json')
        self.assertEqual(len(plan['steps']),3)
        self.assertTrue(all(s['prefix_stable'] is None for s in plan['steps']))
        self.assertTrue(compare(self.source,run/'model_steps.mpd')['identical_part_colour_positions_transforms'])
        self.assertFalse((run/'VERIFY_READY.json').exists())

    def test_single_budget_times_out_preserves_checkpoint_and_does_not_retry(self):
        run=self.root/'plan';run.mkdir();write_json(run/'plan.json',{'steps':[]})
        other=mp.get_context('spawn').Process(target=time.sleep,args=(15,));other.start()
        try:
            started=time.monotonic()
            result=analyze(self.settings,self.source,run,limit_seconds=2,worker_target=phases)
            self.assertEqual(result['status'],'TIMEOUT')
            self.assertLess(time.monotonic()-started,6)
            self.assertTrue((Path(result['checkpoint'])/'phase_one.json').is_file())
            self.assertTrue(other.is_alive(),'unrelated process must remain alive')
            again=analyze(self.settings,self.source,run,limit_seconds=1800,worker_target=failure)
            self.assertEqual(again,result,'a later call must not reset timer/retry')
            if sys.platform=='win32':
                import ctypes
                handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,result['owned_pid'])
                if handle:
                    code=ctypes.c_ulong();ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(code))
                    ctypes.windll.kernel32.CloseHandle(handle);self.assertNotEqual(code.value,259)
        finally:other.terminate();other.join(5)

    def test_physical_failure_and_solver_exception_are_distinct(self):
        run=self.root/'plan';run.mkdir();write_json(run/'plan.json',{'steps':[]})
        result=analyze(self.settings,self.source,run,limit_seconds=5,worker_target=failure)
        self.assertEqual(result['status'],'FAILED')
        changed=replace(self.settings,output_root=self.root/'error')
        result=analyze(changed,self.source,run,limit_seconds=5,worker_target=exception)
        self.assertEqual(result['status'],'ERROR')

    def test_real_worker_final_and_all_steps_on_three_synthetic_bricks(self):
        from lego_workbench.planning import prepare
        run=prepare(self.settings,self.source)
        result=analyze(self.settings,self.source,run,limit_seconds=30)
        self.assertEqual(result['status'],'PASSED',result)
        checkpoint=Path(result['checkpoint'])
        self.assertTrue((checkpoint/'final_analysis.json').is_file())
        self.assertEqual(len(list(checkpoint.glob('step_*.json'))),3)
        self.assertLess(result['used_seconds'],30)

    def test_booklet_is_openable_before_analysis_then_timeout_only_refreshes_text(self):
        from lego_workbench.workflow import process
        from lego_workbench.rendering.framing import FramedRunner
        from PIL import Image,ImageDraw
        from pypdf import PdfReader
        def native(cmd,**kwargs):
            first,last=int(cmd[cmd.index('-f')+1]),int(cmd[cmd.index('-t')+1])
            dest=Path(cmd[cmd.index('-i')+1]);w,h=int(cmd[cmd.index('-w')+1]),int(cmd[cmd.index('-h')+1])
            for n in range(first,last+1):
                target=dest if first==last else dest.with_name(f'frame{n:02d}.png')
                im=Image.new('RGB',(w,h),'white');d=ImageDraw.Draw(im)
                d.rectangle((w//5,h//5,4*w//5,4*h//5),fill='#b8c7cd');d.text((w//3,h//3),'SYNTHETIC',fill='black');im.save(target)
            return subprocess.CompletedProcess(cmd,0,'','')
        def bounded(settings,source,run,limit_seconds):
            books=list(run.glob('booklet_detail_*/instructions_cn.pdf'))
            self.assertEqual(len(books),1);book=books[0].parent
            self.assertGreater(len(PdfReader(books[0]).pages),3)
            self.assertIn('待分析',PdfReader(books[0]).pages[0].extract_text())
            self.saved={p:digest(p) for p in book.rglob('*.png')}
            self.assertIn('说明书已生成，稳定性待分析',stream.getvalue())
            return {'status':'TIMEOUT','used_seconds':.2,'limit_seconds':.2}
        stream=io.StringIO()
        with patch('lego_workbench.rendering.pipeline.FramedRunner',lambda lib,cache:FramedRunner(lib,cache,executor=native)),patch('lego_workbench.bounded_analysis.run',side_effect=bounded),redirect_stdout(stream):
            result=process(self.settings,self.source,'三块合成样例',analysis_seconds=.2)
        self.assertEqual(result['stability_status'],'TIMEOUT')
        self.assertIn('超时未完成',PdfReader(result['files']['pdf']).pages[0].extract_text())
        self.assertTrue(all(digest(p)==sha for p,sha in self.saved.items()))
        with patch('lego_workbench.planning.prepare',side_effect=AssertionError('must reuse')),patch('lego_workbench.bounded_analysis.run',side_effect=AssertionError('must not retry')):
            reused=process(self.settings,self.source,'三块合成样例')
        self.assertTrue(reused['booklet_reused'])

if __name__=='__main__':unittest.main()
