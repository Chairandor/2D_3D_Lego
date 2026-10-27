"""Sequential instructions-first batch, resumable per-model outputs."""
import argparse
from datetime import datetime
from pathlib import Path
import traceback
from .batch import catalog
from .config import PROJECT,read_settings
from .storage import read_json,write_json

def main(argv=None):
    p=argparse.ArgumentParser(description='先说明书后稳定性；逐模型执行并复用成品')
    p.add_argument('--config',type=Path,default=PROJECT/'config/local.json')
    p.add_argument('--manifest',type=Path,default=PROJECT/'config/batch_manifest.json')
    p.add_argument('--state',type=Path)
    p.add_argument('--max-model-minutes',type=float,default=30)
    p.add_argument('--inventory-only',action='store_true')
    p.add_argument('--qa-python') # Kept for the existing PowerShell wrapper.
    args=p.parse_args(argv)
    if not 0<args.max_model_minutes<=30:p.error('每模型累计时限必须大于 0 且不超过 30 分钟')
    settings=read_settings(args.config)
    rows,excluded=catalog(read_json(args.manifest))
    path=args.state or settings.output_root/'batch_instructions_first.json'
    state={'models':rows,'excluded':excluded,'workflow':'instructions-first/v1'}
    write_json(path,state)
    if args.inventory_only:return 0
    from .workflow import process
    failures=0
    for i,row in enumerate(rows):
        print(f'已完成 {i}/{len(rows)}；当前模型：{row["id"]}；当前阶段：读取成品/说明书优先',flush=True)
        try:
            if row['status']!='pending':raise ValueError(row.get('error',row['status']))
            # Never rerun a previously accepted reuse-only model when its cache
            # is missing: fail explicitly instead of rebuilding it.
            if row.get('reuse_only'):
                from .workflow import cached
                if not cached(settings,Path(row['input'])):raise ValueError('已验收模型缺少可核验成品；拒绝重新生成')
            result=process(settings,row['input'],row.get('name'),analysis_seconds=60*args.max_model_minutes)
            row.update(status='complete',generation_status='PASSED',stability_status=result['stability_status'],
                       reused=result.get('booklet_reused',False),files=result['files'],checks=result.get('checks',{}))
        except Exception as error:
            failures+=1;row.update(status='failed',error=str(error),traceback=traceback.format_exc())
            print('该模型未完成：'+str(error),flush=True)
        state['completed']=i+1;state['updated_at']=datetime.now().isoformat();write_json(path,state)
    print(f'已完成 {len(rows)}/{len(rows)}；失败 {failures}；批量清单：{path}',flush=True)
    return 1 if failures else 0
