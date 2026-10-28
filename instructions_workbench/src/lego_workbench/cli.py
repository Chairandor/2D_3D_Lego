"""One model or all three: analysis -> planning -> render -> PDF/HTML -> ZIP."""
import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
import zipfile

from .config import PROJECT, read_settings
from .storage import CORE_FILES, digest, fingerprint, model_lock, read_json, write_json

def delivery_archive(destination, source, run, booklet):
    archive = destination / 'Team_Delivery.zip'
    tmp = destination / 'Team_Delivery.zip.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(source, 'input/' + source.name)
        for n in (*CORE_FILES, 'DRAFT_INPUT_VALIDATED.json', 'READY_FOR_RENDER', 'RECOVERED_SNAPSHOT.json', 'CORE_READY.json', 'FINAL_READY.json',
                  'PLAN_READY.json', 'VERIFY_READY.json', 'final_analysis.json', 'certification.json',
                  'steps.csv', 'bom.csv', 'run_identity.json','source_steps.json'):
            if (run / n).is_file():
                z.write(run / n, 'analysis_and_steps/' + n)
        for n in ('stability_summary.json', 'stability_per_brick.json', 'stability_report.html', 'bom.csv', 'result.json'):
            if (destination / n).is_file():
                z.write(destination / n, n)
        if booklet:
            for p in booklet.rglob('*'):
                if p.is_file() and p.suffix.lower() in ('.png', '.pdf', '.html', '.json'):
                    z.write(p, 'booklet/' + p.relative_to(booklet).as_posix())
    os.replace(tmp, archive)
    return archive

def show_status(settings):
    candidates = sorted(settings.output_root.rglob('progress.json'), key=lambda p: p.stat().st_mtime, reverse=True) if settings.output_root.is_dir() else []
    if not candidates:
        print('尚无新项目进度记录；旧脚本的进度请看原终端。')
        return
    for p in candidates[:3]:
        row = read_json(p)
        print(f"{p.parent.name}：{row['phase']} / {row['status']}；{row['elapsed_seconds']} 秒；LP {row['completed_lp_calls']}；前缀探测 {row['completed_prefix_probes']}")
        print('记录时间：' + datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec='seconds'))
    print('这是最近保存的快照；status=running 的旧记录不保证进程现在仍在运行。')

def process_one(api, settings, model_key, source=None, mode='full', title_override=None):
    from .analysis import export_analysis
    from .engine import checked_run, generate_or_reuse
    from .rendering.pipeline import render_and_booklet
    if source is None:
        model = settings.models[model_key]; source = model['path']; title = model['label']
        expected = model.get('sha256')
    else:
        source = Path(source).resolve(); title = source.stem; expected = None
    if title_override:
        title = title_override
    if not source.is_file() or source.suffix.lower() not in ('.ldr', '.mpd'):
        raise FileNotFoundError('未找到 LDR/MPD 输入：' + str(source))
    source_hash = digest(source)
    if expected and source_hash != expected:
        raise ValueError('原模型与既有实验版本不一致；请核对输入文件。')
    with model_lock(settings.output_root, source_hash):
        run, reused = generate_or_reuse(api, settings, source, model_key, title)
        report, plan, bom = checked_run(run, source)
        destination = settings.output_root / 'deliveries' / (model_key + '_' + source_hash[:12] + '_' + digest(run / 'report.json')[:12])
        summary = export_analysis(run, destination, title, plan, report)
        bom_csv = destination / 'bom.csv'
        with bom_csv.open('w', encoding='utf-8-sig', newline='') as f:
            fields = ['part_key','ldraw_part','colour_code','colour_name','quantity','mass_g']
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader(); writer.writerows(bom['total'])
        print(f"分析摘要：最终模型 {'通过' if summary['final_stability']['stable'] else '未通过'}；中间静力风险 {len(summary['static_risk_steps'])} 步；插入风险 {len(summary['insertion_risk_steps'])} 步。", flush=True)
        booklet = None
        if mode != 'plan':
            args = argparse.Namespace(renderer=settings.renderer, ldraw_dir=settings.ldraw_dir,
                                      title=title, expected_sha256=source_hash)
            booklet = render_and_booklet(args, model_key, run, source)
        if source_hash != digest(source):
            raise RuntimeError('原模型在运行期间发生变化。')
        files = {'stability_report': str(destination / 'stability_report.html'),
                 'stability_summary': str(destination / 'stability_summary.json'),
                 'model_steps': str(run / 'model_steps.mpd'), 'bom': str(bom_csv)}
        if booklet:
            files.update(pdf=str(booklet / 'instructions_cn.pdf'), html=str(booklet / 'instructions_cn.html'))
        result = {'model': title, 'input_sha256': source_hash, 'analysis_and_plan_reused': reused,
                  'instruction_certified': summary['instruction_certified'], 'booklet_complete': bool(booklet),
                  'status': 'DIGITAL_CHECKS_PASSED' if summary['instruction_certified'] and booklet else 'DRAFT_REVIEW_REQUIRED',
                  'files': files}
        result['team_delivery'] = str(destination / 'Team_Delivery.zip')
        write_json(destination / 'result.json', result)
        archive = delivery_archive(destination, source, run, booklet)
        return result

def main(argv=None):
    p = argparse.ArgumentParser(description='LDR/MPD：稳定性分析 + 中文说明书；复用既有结果。')
    p.add_argument('--config', type=Path, default=PROJECT / 'config' / 'local.json')
    p.add_argument('--model', choices=['chicken', 'bunny', 'mpd', 'all'], default='all')
    p.add_argument('--input', type=Path, help='队友的任意 LDR/MPD；严格导入，不自动近似。')
    p.add_argument('--title', help='说明书显示名称；不改变模型几何。')
    p.add_argument('--mode', choices=['instructions-first','full','legacy-full', 'plan', 'check', 'status','export-draft','prepare','verify-steps','source-plan'], default='instructions-first')
    p.add_argument('--analysis-seconds',type=float,default=1800,help='整个模型稳定性累计时限（0–1800 秒），短值用于超时验收。')
    p.add_argument('--plan-run',type=Path,help='已保存的计划检查点；用于独立草稿图文导出。')
    args = p.parse_args(argv)
    settings = None
    try:
        settings = read_settings(args.config)
        if args.mode in ('instructions-first','full'):
            if not args.input:raise ValueError('默认流程必须指定 --input；不会自动运行旧模型或 all')
            from .workflow import process
            result=process(settings,args.input,args.title,analysis_seconds=args.analysis_seconds,plan_run=args.plan_run)
            write_json(settings.output_root/'LATEST_WORKFLOW.json',result)
            return 0 if result['stability_status']=='PASSED' else 2
        if args.mode=='source-plan':
            if not args.input:raise ValueError('source-plan 需要 --input；只导入原始明确 STEP')
            from .source_plan import import_source_plan
            run=import_source_plan(settings,args.input,args.plan_run)
            print('PLAN_CHECKPOINT='+str(run));return 0
        if args.mode=='export-draft':
            if not args.input or not args.plan_run:raise ValueError('export-draft 需要 --input 和 --plan-run')
            from .draft import export_draft
            result=export_draft(settings,args.input,args.plan_run,args.title or args.input.stem)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        if args.mode=='verify-steps':
            if not args.input or not args.plan_run:raise ValueError('verify-steps 需要 --input 和 --plan-run')
            from .bounded_analysis import run as bounded
            with model_lock(settings.output_root,digest(args.input)):
                result=bounded(settings,args.input,args.plan_run,limit_seconds=args.analysis_seconds)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status']=='PASSED' else 2
        if args.mode == 'status':
            show_status(settings); return 0
        from .environment import check
        os.environ['LEGOLIZATION_RENDERER'] = str(settings.renderer)
        os.environ['LDRAWDIR'] = str(settings.ldraw_dir)
        api = check(settings, require_renderer=args.mode != 'plan')
        if args.mode == 'check':
            print('环境检查通过；未读取或求解模型。'); return 0
        if args.mode=='prepare':
            if not args.input:raise ValueError('prepare 仅接受明确的 --input，不运行旧模型全集')
            from .engine import generate_or_reuse
            with model_lock(settings.output_root,digest(args.input)):
                run,reused=generate_or_reuse(api,settings,args.input.resolve(),'custom',args.title or args.input.stem,prepare_only=True)
            print('PLAN_CHECKPOINT='+str(run),flush=True)
            return 0
        settings.output_root.mkdir(parents=True, exist_ok=True)
        keys = ['custom'] if args.input else ['chicken', 'bunny', 'mpd'] if args.model == 'all' else [args.model]
        results = []; failures = []
        for i, key in enumerate(keys, 1):
            print(f'\n当前模型 {i}/{len(keys)}：{key}', flush=True)
            try:
                result = process_one(api, settings, key, args.input, args.mode, args.title)
                results.append(result)
                write_json(settings.output_root / 'LATEST.json', {'completed': results, 'failed': failures})
            except Exception as error:
                payload = {'model': key, 'error': str(error), 'traceback': traceback.format_exc(), 'time': datetime.now().isoformat()}
                if hasattr(error, 'details'):
                    from dataclasses import asdict
                    payload['import_problems'] = [asdict(x) for x in error.details]
                errors = settings.output_root / 'errors'
                error_path = errors / (key + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.json')
                write_json(error_path, payload)
                failures.append({'model': key, 'error': str(error), 'diagnostic': str(error_path)})
                print('停止该模型：' + str(error) + '\n诊断：' + str(error_path), flush=True)
                write_json(settings.output_root / 'LATEST.json', {'completed': results, 'failed': failures})
        print('\n分析与说明书输出汇总', flush=True)
        for r in results:
            print(r['model'] + '：' + ('图文完成；数字检查通过' if r['status'] == 'DIGITAL_CHECKS_PASSED' else '输出已保存；稳定性待检查草稿'))
            for name, path in r['files'].items():
                print('  ' + name + '：' + path)
            print('  队友交付包：' + r['team_delivery'])
        print('稳定性优化未执行；旧 StableLego 实验未重跑。', flush=True)
        if failures:
            return 1
        return 0 if all(r['status'] == 'DIGITAL_CHECKS_PASSED' for r in results) else 2
    except Exception as error:
        if settings is not None:
            payload={'mode':args.mode,'input':str(args.input) if args.input else None,'plan_run':str(args.plan_run) if args.plan_run else None,
                     'error':str(error),'traceback':traceback.format_exc(),'time':datetime.now().isoformat()}
            if hasattr(error,'details'):
                from dataclasses import asdict
                payload['import_problems']=[asdict(x) for x in error.details]
            diagnostic=settings.output_root/'errors'/(args.mode+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
            write_json(diagnostic,payload)
            print('诊断已保存：'+str(diagnostic),file=sys.stderr)
        print('停止：' + str(error), file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
