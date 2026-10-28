"""Sequential new-model batches. Completed results are checked without solving or rendering."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from .config import PROJECT, read_settings
from .storage import digest, model_lock, read_json, write_json

EXCLUDED_HASH = 'dbf917b222136797636177c8ce1a104581e050f962f94c6b04ccc1b33790d056'

def resolve(value, base=PROJECT):
    p = Path(value)
    return p.resolve() if p.is_absolute() else (base / p).resolve()

def catalog(manifest):
    seen = {}; rows = []; excluded = list(manifest.get('excluded', []))
    for entry in manifest['models']:
        row = dict(entry); source = resolve(row['input'])
        row['input'] = str(source)
        if '7ken' in [s.lower() for s in source.parts]:
            excluded.append(dict(row, reason='7ken 误打包旧版')); continue
        if not source.is_file():
            rows.append(dict(row, status='missing_input', error='缺少输入：'+str(source))); continue
        actual = digest(source)
        if actual == EXCLUDED_HASH:
            excluded.append(dict(row, reason='原 model(1).mpd 输入哈希，禁止运行')); continue
        if actual in seen:
            excluded.append(dict(row, reason='输入哈希重复', duplicate_of=seen[actual])); continue
        if row.get('sha256') and row['sha256'] != actual:
            rows.append(dict(row, status='hash_mismatch', error='输入哈希与清单不符')); continue
        seen[actual] = row['id']; row['sha256'] = actual
        row['status'] = 'pending'; rows.append(row)
    return rows, excluded

def result_for(row, settings):
    def matches(path):
        result=read_json(path)
        if result.get('input_sha256') != row['sha256'] or not result.get('booklet_complete'):
            return False
        summary_path=resolve(result['files']['stability_summary'],path.parent)
        summary=read_json(summary_path)
        run=resolve(summary['source_run'],summary_path.parent)
        if result.get('export_mode')=='graphics_only_draft':
            report=read_json(run/'DRAFT_INPUT_VALIDATED.json')
            config=report['config']
        else:
            report=read_json(run/'report.json')
            config=report['instructions_config']
        return (report['ground']==settings.ground and config['target_step_size']==settings.step_size
                and config['stability_policy']==settings.policy and report['package_version']=='0.6.0')
    if row.get('completed_result'):
        path = resolve(row['completed_result'])
        if path.is_file() and matches(path):
            return path
        if row.get('reuse_only'):
            raise FileNotFoundError('已验收模型缺少完成结果；不自动重新运行：'+str(path))
    for path in (settings.output_root / 'deliveries').glob('*/result.json'):
        try:
            result = read_json(path)
            if result.get('input_sha256') == row['sha256'] and result.get('booklet_complete') and matches(path):
                return path
        except (OSError, ValueError, KeyError):
            continue
    if row.get('reuse_only'):
        raise ValueError('已验收模型缺少可核验结果；不重新规划/求解/渲染。')
    return None

def verify(row, result_path, qa_python, state_dir):
    output = state_dir / 'checks' / (row['id']+'.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [qa_python, str(PROJECT/'src/lego_workbench/delivery_check.py'),
           '--input', row['input'], '--result', str(result_path), '--output', str(output)]
    p = subprocess.run(cmd, text=True, encoding='utf-8', errors='replace', capture_output=True)
    if p.returncode:
        raise RuntimeError('交付校验失败：'+p.stdout[-1500:]+p.stderr[-1500:])
    check = read_json(output)
    if row.get('expected_pieces') is not None and check['pieces'] != row['expected_pieces']:
        raise ValueError(f'输入审计与导入件数不符：{row["expected_pieces"]} / {check["pieces"]}；不标记通过。')
    return check

def save_state(path, state):
    state['updated_at'] = datetime.now().isoformat(timespec='seconds')
    write_json(path, state)
    fields = ['id','name','studs','input','sha256','status','stage','reused','pieces','steps','pdf_pages',
              'generation_flow_status','stability_status','step_verification_status','result_path','error']
    with path.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(state['models'])

def completed(state):
    return sum(row['status'] in ('complete','failed','missing_input','hash_mismatch') for row in state['models'])

def restore_packaged_checkpoint(row,settings):
    if not row.get('resume_checkpoint'):return
    source=resolve(row['resume_checkpoint'])
    identity=read_json(source/'run_identity.json')
    if identity['source_sha256']!=row['sha256'] or identity['ground']!=settings.ground:
        raise ValueError('交付检查点与当前模型/地面设置不符。')
    config=identity['config']
    if config['target_step_size']!=settings.step_size or config['stability_policy']!=settings.policy:
        raise ValueError('交付检查点与当前步骤配置不符。')
    name='custom_'+row['sha256'][:12]+'_'+identity['fingerprint'][:12]
    destination=settings.output_root/name
    if not destination.exists():
        settings.output_root.mkdir(parents=True,exist_ok=True)
        shutil.copytree(source,destination)
        print(row['name']+'：恢复交付包中已完成阶段的检查点；不覆盖本机已有结果。',flush=True)

def plan_checkpoint(row,settings):
    from .draft import audit_plan
    candidates=[]
    if row.get('resume_checkpoint'):candidates.append(resolve(row['resume_checkpoint']))
    for pattern in ('custom_'+row['sha256'][:12]+'_*','draft_'+row['sha256'][:12]+'_*','source_plan_'+row['sha256'][:12]+'_*'):
        candidates.extend(settings.output_root.glob(pattern))
    for folder in sorted(candidates,key=lambda p:p.stat().st_mtime if p.exists() else 0,reverse=True):
        if not (folder/'plan.json').is_file():continue
        try:
            if folder.name.startswith('source_plan_') and 'including child' not in read_json(folder/'DRAFT_INPUT_VALIDATED.json').get('plan_origin',''):continue
            _,_,identity,_,_=audit_plan(folder,Path(row['input']))
            config=identity['config']
            if identity['ground']==settings.ground and config['target_step_size']==settings.step_size and config['stability_policy']==settings.policy:
                return folder
        except (OSError,ValueError,KeyError,AssertionError):continue
    return None

def invoke(row, args, state, path, settings, *, mode='full', plan_run=None):
    cmd = ['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(PROJECT/'Run.ps1'),
           '-Config',str(args.config.resolve()),'-Python',sys.executable,'-InputModel',row['input'],'-Title',row['name']]
    if mode!='full':cmd.extend(['-Mode',mode])
    if plan_run:cmd.extend(['-PlanRun',str(plan_run)])
    log_dir = path.parent/'logs'; log_dir.mkdir(exist_ok=True)
    events = queue.Queue()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding='utf-8', errors='replace', cwd=PROJECT)
    row['pid'] = p.pid; row['child_pid'] = p.pid; row['stage'] = '启动统一入口'; save_state(path,state)
    def read_output():
        for line in p.stdout:
            events.put(line)
        events.put(None)
    threading.Thread(target=read_output, daemon=True).start()
    last_heartbeat = 0; ended = False
    log_path = log_dir/(row['id']+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.log')
    row['log'] = str(log_path)
    began=time.monotonic(); timed_out=False
    with log_path.open('w', encoding='utf-8') as log:
        while not ended or p.poll() is None:
            if mode!='export-draft' and args.max_model_minutes>0 and not timed_out and p.poll() is None and time.monotonic()-began>=60*args.max_model_minutes:
                diagnostic={'model':row['id'],'input_sha256':row['sha256'],'reason':'USER_MODEL_WALL_TIME_LIMIT',
                            'limit_seconds':60*args.max_model_minutes,'phase':row['stage'],'pid':p.pid}
                write_json(path.parent/'timeouts'/(row['id']+'.json'),diagnostic)
                # Popen owns this exact child; unrelated/previous tasks are never targeted.
                subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,check=False)
                timed_out=True
            try:
                line = events.get(timeout=1)
                if line is None:
                    ended = True
                else:
                    log.write(line); log.flush()
                    if line.strip():
                        print(f'[{completed(state)}/{len(state["models"])}] {row["name"]} | {line.rstrip()}',flush=True)
                        if any(tag in line for tag in ('[1/6]','[2/6]','[3/6]','[4/6]','[5/6]','[6/6]','复用','完成：')):
                            row['stage'] = line.strip(); save_state(path,state)
            except queue.Empty:
                pass
            if time.monotonic()-last_heartbeat >= 20:
                print(f'[{completed(state)}/{len(state["models"])}] 当前模型：{row["name"]}；当前阶段：{row["stage"]}',flush=True)
                save_state(path,state); last_heartbeat = time.monotonic()
    p.stdout.close()
    row['exit_code'] = p.wait(); row.pop('pid',None)
    if timed_out:
        raise TimeoutError(f'运行达到 {args.max_model_minutes} 分钟上限；已保存阶段结果，未完成阶段待续跑。')
    if mode=='prepare':
        checkpoint=plan_checkpoint(row,settings)
        if checkpoint is None:raise RuntimeError('没有完整步骤计划；已保存诊断和已完成阶段，不冒充完整说明书。')
        return checkpoint
    result_path = result_for(row, settings)
    if result_path is None:
        latest = read_json(settings.output_root/'LATEST.json') if (settings.output_root/'LATEST.json').is_file() else {}
        raise RuntimeError(f'统一入口未完成，退出码 {row["exit_code"]}；诊断：{latest.get("failed", [])}')
    return result_path

def graphics_first(row,args,state,path,settings):
    checkpoint=plan_checkpoint(row,settings)
    if checkpoint is None:
        try:checkpoint=invoke(row,args,state,path,settings,mode='prepare')
        except TimeoutError:
            checkpoint=plan_checkpoint(row,settings)
            if checkpoint is None:raise
            row['previous_verification_error']='分析/规划阶段到达时限；完整计划已保存，继续独立草稿导出。'
    row['plan_checkpoint']=str(checkpoint)
    row['step_verification_status']='INCOMPLETE'
    return invoke(row,args,state,path,settings,mode='export-draft',plan_run=checkpoint)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--state',type=Path)
    parser.add_argument('--qa-python',default=sys.executable)
    parser.add_argument('--inventory-only',action='store_true')
    parser.add_argument('--max-model-minutes',type=float,default=30)
    args = parser.parse_args(argv)
    settings = read_settings(args.config)
    state_path = args.state or settings.output_root/'batch_20261019'/'batch_status.json'
    rows, excluded = catalog(read_json(args.manifest))
    state = {'schema':'lego-batch/v1','manifest':str(args.manifest.resolve()),'models':rows,'excluded':excluded}
    if state_path.is_file():
        previous = {r['sha256']:r for r in read_json(state_path)['models'] if r.get('sha256')}
        for row in rows:
            old = previous.get(row.get('sha256'))
            if old:
                if old.get('result_path'):
                    row['completed_result'] = old['result_path']
                if args.inventory_only:
                    row.update({k:v for k,v in old.items() if k not in ('input','sha256','id')})
    state_path.parent.mkdir(parents=True,exist_ok=True)
    if args.inventory_only:
        write_json(state_path.parent/'batch_inventory.json',state)
        print(json.dumps(state,ensure_ascii=False,indent=2)); return 0
    # A batch has its own OS lock as well as the per-model locks in Run.ps1.
    with model_lock(state_path.parent, 'sequential_batch'):
        save_state(state_path,state)
        # Validate QA availability before expensive stages; no changes to the original venv.
        test = subprocess.run([args.qa_python,'-c','import pypdf, PIL'],capture_output=True,text=True)
        if test.returncode:
            raise RuntimeError('QA Python needs pypdf and Pillow; use -QaPython or run Setup in the project environment.')
        for row in rows:
            if row['status'] != 'pending':
                continue
            try:
                row['stage'] = '检查已有结果'; row['status'] = 'running'; save_state(state_path,state)
                result_path = result_for(row,settings)
                reused = result_path is not None
                if result_path is None:
                    restore_packaged_checkpoint(row,settings)
                    result_path = graphics_first(row,args,state,state_path,settings)
                row['stage'] = '零件覆盖、图片、PDF、HTML 与 ZIP 校验'; save_state(state_path,state)
                check = verify(row,result_path,args.qa_python,state_path.parent)
                row.update(status='complete',stage='数字生成与覆盖检查完成，待视觉验收',reused=reused,
                           pieces=check['pieces'],steps=check['steps'],pdf_pages=check['pdf_pages'],
                           generation_flow_status='CHECKS_PASSED_VISUAL_REVIEW_PENDING',
                           step_verification_status=check.get('step_verification_status','COMPLETED'),
                           stability_status=check['stability_status'],result_path=str(result_path),verification=check)
                if row.get('visual_accepted'):
                    row['generation_flow_status']='PASSED'; row['stage']='复用用户已验收成品'
                print(f'[{completed(state)}/{len(rows)}] {row["name"]} 已保存：{row["pieces"]} 件、{row["steps"]} 步、{row["pdf_pages"]} 页；稳定性 {row["stability_status"]}',flush=True)
            except Exception as error:
                timeout=state_path.parent/'timeouts'/(row['id']+'.json')
                if timeout.is_file():
                    detail=read_json(timeout)
                    if detail.get('pid')==row.get('child_pid'):
                        error=TimeoutError(f'达到 {detail["limit_seconds"]//60} 分钟模型时限；阶段：{detail.get("phase")}; 已保留检查点。')
                row.update(status='failed',stage='超时未完成，检查点已保存' if isinstance(error,TimeoutError) else '失败，诊断已保存',
                           generation_flow_status='INCOMPLETE_TIMEOUT' if isinstance(error,TimeoutError) else 'FAILED',error=str(error))
                write_json(state_path.parent/'errors'/(row['id']+'.json'),row)
                print(f'[{completed(state)}/{len(rows)}] {row["name"]} 失败：{error}',flush=True)
            save_state(state_path,state)
        state['finished'] = True; save_state(state_path,state)
        print('批量完成，清单：'+str(state_path),flush=True)
        return 1 if any(r['status'] != 'complete' for r in rows) else 0
