"""One source hash, one cumulative wall-clock budget, one owned worker process.

The parent enforces the deadline even while a native LP call is uninterruptible.
No user/legacy processes are searched for or terminated. Terminal results never
automatically retry. Budget includes import, final analysis and every prefix.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
from .storage import digest, fingerprint, read_json, write_json

LABELS={'PENDING':'待分析','PASSED':'通过','FAILED':'未通过','TIMEOUT':'超时未完成','ERROR':'求解异常'}
TERMINAL={'PASSED','FAILED','TIMEOUT','ERROR'}

def worker(source,run,destination,ldraw_dir,ground):
    """Private 0.6.0 prefix adapter, pinned and shipped with vendor source."""
    destination=Path(destination)
    try:
        from .compat.api import load_api
        from .environment import ensure_palette
        from .analysis import analyze_layout
        from .rendering.framing import restore_plan
        from legolization.instructions.verification import _step_layout
        ensure_palette(Path(ldraw_dir));api=load_api()
        loaded=api['import_ldraw'](Path(source),ground=ground)
        if not loaded.load_complete:raise ValueError('分析阶段严格导入不完整')
        plan,_=restore_plan(read_json(Path(run)/'plan.json'))
        config=api['SolverConfig']()
        write_json(destination/'solver_config.json',{'solver':asdict(config),'analysis_ground':ground,
            'scope':'final plus cold self-weight static prefixes; insertion loading is not certified'})
        def solve(layout,name):
            path=destination/name
            if path.is_file():return read_json(path)
            write_json(destination/'worker_progress.json',{'phase':name,'pid':os.getpid(),'status':'running'})
            result=analyze_layout(api,layout,config)
            if result['final_stability']['status'] not in ('optimal','optimal_inaccurate'):
                # Keep failed solver output without treating it as physical failure/pass.
                write_json(path,result)
                raise RuntimeError('LP 未正常完成：'+str(result['final_stability']['status']))
            write_json(path,result)
            return result
        final=solve(loaded.layout,'final_analysis.json')
        placed=set();subs={s.name:s for s in plan.subassemblies};sub_placed={n:set() for n in subs}
        results=[]
        for step in plan.steps:
            layout,action,ids=_step_layout(loaded.layout,step,placed=placed,subassemblies=subs,sub_placed=sub_placed)
            row=solve(layout,f'step_{step.index:04d}.json')
            results.append({'index':step.index,'stable':row['final_stability']['stable'],'action':action})
        passed=bool(final['final_stability']['stable'] and all(s['stable'] for s in results)
                    and final['topology']['component_count']==1 and final['topology']['floating_count']==0)
        write_json(destination/'worker_result.json',{'status':'PASSED' if passed else 'FAILED','steps':results})
    except BaseException as error:
        write_json(destination/'worker_result.json',{'status':'ERROR','error':str(error),'traceback':traceback.format_exc()})

def stop_owned(process):
    if process.is_alive():
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False,
                           creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
        if process.is_alive():process.terminate()
        process.join(10)
        if process.is_alive():process.kill();process.join(5)
    if process.is_alive():raise RuntimeError('无法终止本次创建的求解子进程')

def run(settings,source,plan_run,*,limit_seconds=1800,worker_target=worker):
    limit_seconds=float(limit_seconds)
    if not 0 < limit_seconds <= 1800:raise ValueError('累计稳定性时限必须在 0–1800 秒之间')
    sha=digest(source);budget_path=settings.output_root/'analysis_budget'/sha/'budget.json'
    folder=budget_path.parent
    folder.mkdir(parents=True,exist_ok=True)
    signature=fingerprint({'source':sha,'plan':digest(Path(plan_run)/'plan.json'),'ground':settings.ground,'engine':'0.6.0'})
    old=read_json(budget_path) if budget_path.is_file() else {}
    # A parent interrupted previously may have left a worker alive. Never spawn
    # a duplicate or kill an unowned PID. Save a conservative terminal outcome.
    if old.get('running'):
        elapsed=max(0,time.time()-old['started_epoch'])
        old.update(used_seconds=min(old['limit_seconds'],old['used_before']+elapsed),running=False,
                   status='TIMEOUT' if old['used_before']+elapsed>=old['limit_seconds'] else 'ERROR',
                   error='上次监督进程中断；保留子进程记录，不自动重试。请检查记录中的 PID。')
        write_json(budget_path,old)
    if old.get('status') in TERMINAL:
        if old.get('signature')!=signature:
            return dict(old,status='ERROR',error='同一输入已有终结的累计预算记录，计划/分析设置不同；保留原记录，不自动重算。')
        return old
    cap=min(limit_seconds,old.get('limit_seconds',1800));used=old.get('used_seconds',0.)
    if used>=cap:
        result=dict(old,status='TIMEOUT',used_seconds=used,limit_seconds=cap,running=False)
        write_json(budget_path,result);return result
    checkpoint=folder/signature[:16]
    checkpoint.mkdir(exist_ok=True)
    # Reuse a verified final analysis from an existing plan checkpoint. The
    # budget remains source-wide; copying a result never creates extra time.
    origin=Path(plan_run)
    if (origin/'final_analysis.json').is_file() and not (checkpoint/'final_analysis.json').exists():
        identity=read_json(origin/'run_identity.json')
        final=read_json(origin/'final_analysis.json')
        from .compat.api import load_api
        expected=asdict(load_api()['SolverConfig']())
        if identity['source_sha256']==sha and identity.get('ground')==settings.ground and final.get('solver_config')==expected:
            marker=read_json(origin/'DRAFT_INPUT_VALIDATED.json') if (origin/'DRAFT_INPUT_VALIDATED.json').is_file() else read_json(origin/'FINAL_READY.json')
            assert marker['files']['final_analysis.json']==digest(origin/'final_analysis.json')
            shutil.copy2(origin/'final_analysis.json',checkpoint/'final_analysis.json')
    state={'input_sha256':sha,'signature':signature,'limit_seconds':cap,'used_before':used,
           'used_seconds':used,'started_epoch':time.time(),'running':True,'status':'PENDING',
           'checkpoint':str(checkpoint),'scope':'累计最终模型和全部步骤自重静力；不含实物/插入认证'}
    start=time.monotonic();process=mp.get_context('spawn').Process(target=worker_target,
        args=(str(source),str(plan_run),str(checkpoint),str(settings.ldraw_dir),settings.ground))
    write_json(budget_path,state)
    try:
        process.start();state['owned_pid']=process.pid;write_json(budget_path,state)
        last_heartbeat=start;last_display=start
        while process.is_alive():
            elapsed=time.monotonic()-start
            if used+elapsed>=cap:
                stop_owned(process);state.update(status='TIMEOUT',error='累计稳定性预算耗尽；未自动重试')
                break
            if time.monotonic()-last_heartbeat>=1:
                state['used_seconds']=used+elapsed;write_json(budget_path,state);last_heartbeat=time.monotonic()
            if time.monotonic()-last_display>=20:
                progress=checkpoint/'worker_progress.json'
                phase=read_json(progress).get('phase','启动') if progress.is_file() else '启动'
                print(f'当前阶段：稳定性 {phase}；累计 {used+elapsed:.0f}/{cap:.0f} 秒；说明书已可查看。',flush=True)
                last_display=time.monotonic()
            process.join(min(.1,max(.001,cap-used-elapsed)))
        else:
            result=checkpoint/'worker_result.json'
            state.update(read_json(result) if result.is_file() else {'status':'ERROR','error':'求解子进程退出但没有完整结果'})
    except BaseException:
        if process.pid is not None:stop_owned(process)
        state.update(status='ERROR',error=traceback.format_exc())
        raise
    finally:
        state.update(used_seconds=used+time.monotonic()-start,running=False,finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(budget_path,state)
    return state
