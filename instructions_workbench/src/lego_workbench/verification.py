"""Standalone bounded verification of a saved plan, independent of booklets."""
from dataclasses import asdict
import multiprocessing
from pathlib import Path
import subprocess
import time
from .storage import digest,fingerprint,model_lock,read_json,write_json

def _worker(settings,source,origin,target):
    from .draft import audit_plan
    from .compat.api import load_api
    from .engine import instruction_config
    from .rendering.framing import restore_plan
    from .progress import monitor
    try:
        with model_lock(settings.output_root,digest(source)):
            plan,bom,identity,_,_=audit_plan(origin,source)
            api=load_api();config=instruction_config(api,settings)
            assert identity['ground']==settings.ground and identity['config']==asdict(config),'分析配置不同，不复用旧计划验证'
            loaded=api['import_ldraw'](source,ground=settings.ground)
            assert loaded.load_complete and len(loaded.layout)==bom['brick_count']
            restored,_=restore_plan(plan)
            with monitor(target,'独立步骤验证'):
                cert=api['certify_instructions'](loaded.layout,restored,config=config)
            write_json(target/'certification.json',asdict(cert))
            write_json(target/'verification_result.json',{'status':'COMPLETED','input_sha256':digest(source),
                       'plan_sha256':digest(origin/'plan.json'),'certification':asdict(cert),
                       'meaning':'实际执行的独立逐步验证；不改写既有说明书、风险页或认证标记。'})
    except Exception as error:
        write_json(target/'verification_result.json',{'status':'FAILED','error':str(error),'certification':None})
        raise

def verify_saved_plan(settings,source,origin,max_seconds=1800):
    source=Path(source).resolve();origin=Path(origin).resolve()
    from .draft import audit_plan
    _,_,identity,_,_=audit_plan(origin,source)
    assert identity['ground']==settings.ground and identity['config']['target_step_size']==settings.step_size and identity['config']['stability_policy']==settings.policy,'计划分析设置不同'
    key=fingerprint({'source':digest(source),'plan':digest(origin/'plan.json'),'config':identity['config'],
                     'ground':identity['ground'],'mode':'standalone-verification/v1'})
    target=settings.output_root/('verification_'+key[:16]);target.mkdir(parents=True,exist_ok=True)
    status=target/'verification_result.json'
    if status.is_file() and read_json(status).get('status')=='COMPLETED':return read_json(status)
    with model_lock(settings.output_root,'verify_request_'+digest(source)):
        process=multiprocessing.get_context('spawn').Process(target=_worker,args=(settings,source,origin,target))
        process.start();began=time.monotonic();last=0
        while process.is_alive():
            process.join(timeout=min(5,max_seconds))
            elapsed=time.monotonic()-began
            if elapsed>=max_seconds and process.is_alive():
                # Only this exact, owned multiprocessing child is stopped.
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False)
                process.join(timeout=5)
                write_json(status,{'status':'INCOMPLETE_TIMEOUT','input_sha256':digest(source),
                           'plan_sha256':digest(origin/'plan.json'),'certification':None,'limit_seconds':max_seconds,
                           'meaning':'未完成验证；说明书与原计划保留。'})
                break
            if elapsed-last>=20:
                print(f'独立静力验证 {int(elapsed)} 秒 / {max_seconds} 秒；说明书已保留。',flush=True);last=elapsed
        if not status.is_file():raise RuntimeError('独立验证进程未产生结果；检查 progress.json 和终端诊断')
    return dict(read_json(status),result_path=str(status))
