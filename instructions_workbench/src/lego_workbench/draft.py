"""Explicit graphics-only export from a saved plan. Never plans or solves."""
from dataclasses import asdict
import csv
from html import escape
from importlib.metadata import version
from pathlib import Path
import shutil
import re

from .booklet.pdf import validate
from .plan_coverage import validate_nonblank_steps
from .ldraw_geometry import compare
from .storage import digest,fingerprint,model_lock,read_json,write_json

from .verification import verify_saved_plan

DRAFT_FILES=('plan.json','bom.json','source_map.json','model_steps.mpd')

def audit_plan(origin,source):
    origin=Path(origin);source=Path(source)
    identity=read_json(origin/'run_identity.json')
    assert identity['source_sha256']==digest(source),'计划来源与输入哈希不符'
    plan=read_json(origin/'plan.json');bom=read_json(origin/'bom.json')
    validate(plan,bom)
    validate_nonblank_steps(plan)
    refs=read_json(origin/'source_map.json')
    ids=[i for s in plan['steps'] for i in s['brick_ids']]
    assert set(ids)==set(map(int,refs)) and len(refs)==bom['brick_count'],'源映射与步骤覆盖不符'
    # Existing planning markers are evidence; no verification marker is created.
    stamp=read_json(origin/'PLAN_READY.json') if (origin/'PLAN_READY.json').is_file() else None
    if stamp:
        assert stamp['fingerprint']==identity['fingerprint']
        for name,expected in stamp['files'].items():
            if (origin/name).is_file():assert digest(origin/name)==expected,'计划检查点已改变：'+name
            elif name!='model_steps.mpd':raise FileNotFoundError(origin/name)
    elif (origin/'DRAFT_INPUT_VALIDATED.json').is_file():
        checked_draft(origin,source)
    else:
        raise ValueError('缺少原始规划检查点或明确的草稿输入核验记录')
    final=read_json(origin/'final_analysis.json') if (origin/'final_analysis.json').is_file() else None
    if final and (origin/'FINAL_READY.json').is_file():
        marker=read_json(origin/'FINAL_READY.json')
        assert marker['fingerprint']==identity['fingerprint'] and marker['files']['final_analysis.json']==digest(origin/'final_analysis.json')
    geometry=compare(source,origin/'model_steps.mpd') if (origin/'model_steps.mpd').is_file() else None
    return plan,bom,identity,final,geometry

def checked_draft(run,source):
    meta=read_json(run/'DRAFT_INPUT_VALIDATED.json')
    assert meta['schema']=='lego-graphics-only-input/v1' and meta['input_sha256']==digest(source)
    assert meta['step_verification_status']=='INCOMPLETE'
    assert not any((run/n).exists() for n in ('report.json','CORE_READY.json','VERIFY_READY.json','READY_FOR_RENDER')),'草稿不得冒充原验证完成结果'
    for name,expected in meta['files'].items():assert digest(run/name)==expected
    plan=read_json(run/'plan.json');bom=read_json(run/'bom.json');validate(plan,bom)
    assert compare(source,run/'model_steps.mpd')['source_records']==bom['brick_count']
    return {'instruction_certified':False,'verification_incomplete':True},plan,bom

def analysis_report(destination,run,title,plan,bom,identity,final):
    risks=[s['index'] for s in plan['steps'] if not s['prefix_stable']]
    insertion=[s['index'] for s in plan['steps'] if s.get('insertion_fragile')]
    insertion=sorted(set(insertion)|{int(m.group(1)) for w in plan.get('warnings',[]) if (m:=re.match(r'^step (\d+): insertion-fragile',w))})
    summary={'model':title,'engine':'legolization 0.6.0','source_run':str(run),
             'input_sha256':identity['source_sha256'],'pieces':bom['brick_count'],'steps':len(plan['steps']),
             'instruction_certified':False,'certification':None,'step_verification_status':'INCOMPLETE',
             'final_stability':final['final_stability'] if final else None,
             'topology':final['topology'] if final else None,
             'static_risk_steps':risks,'insertion_risk_steps':insertion,
             'risk_basis':'saved_plan_flags; full per-step verification not completed',
             'unverified_steps':[s['index'] for s in plan['steps']],
             'solver_config':identity['config']['solver'],'stability_optimization':False,
             'meaning':'逐步骤静力未完成验证；计划中的已知风险保留；图文完成不代表稳定性通过。'}
    destination.mkdir(parents=True,exist_ok=True)
    write_json(destination/'stability_summary.json',summary)
    if final:write_json(destination/'stability_per_brick.json',final)
    rows=''.join(f'<tr><td>{s["index"]}</td><td>未完成验证</td><td>{"计划标记静力风险" if not s["prefix_stable"] else "没有计划风险标记；不能视为通过"}</td><td>{"计划标记插入风险" if s["index"] in insertion else "未标记；不能视为通过"}</td></tr>' for s in plan['steps'])
    verdict=('通过' if final['final_stability']['stable'] else '未通过') if final else '未完成分析'
    html=f'<!doctype html><meta charset="utf-8"><title>{escape(title)}未完成验证报告</title><style>body{{font:16px system-ui;max-width:1100px;margin:30px auto;line-height:1.8}}td,th{{padding:6px;border-bottom:1px solid #ddd}}.warning{{color:#a45225}}</style><h1>{escape(title)} · 待验证草稿</h1><p>{bom["brick_count"]} 件 / {len(plan["steps"])} 步。最终模型静力：{verdict}（复用已有分析）。</p><p class="warning">逐步骤静力：未完成验证。未生成或伪造认证结果。图文导出、覆盖检查与稳定性验证分别记录。</p><p>保留计划静力风险提示 {len(risks)} 步、插入风险提示 {len(insertion)} 步；未标记风险不等于通过。模型几何没有改变，没有运行稳定性优化。</p><table><tr><th>步骤</th><th>逐步验证</th><th>已知静力提示</th><th>已知插入提示</th></tr>{rows}</table>'
    (destination/'stability_report.html').write_text(html,encoding='utf-8')
    return summary

def export_draft(settings,source,origin,title):
    source=Path(source).resolve();origin=Path(origin).resolve()
    assert version('legolization')=='0.6.0'
    assert settings.renderer.is_file() and (settings.ldraw_dir/'LDConfig.ldr').is_file()
    from .environment import ensure_palette
    ensure_palette(settings.ldraw_dir)
    plan,bom,identity,final,geometry=audit_plan(origin,source)
    print(f'草稿输入核验：{bom["brick_count"]} 件，{len(plan["steps"])} 步，逐件覆盖和 BOM 一致；不规划、不求解。',flush=True)
    originals={n:digest(origin/n) for n in (*DRAFT_FILES,'final_analysis.json','run_identity.json','source_steps.json') if (origin/n).is_file()}
    key=fingerprint({'mode':'graphics-only-draft/v1','input':digest(source),'files':originals})
    run=settings.output_root/('draft_'+digest(source)[:12]+'_'+key[:12])
    with model_lock(settings.output_root,'graphics_'+digest(source)):
        destination=settings.output_root/'deliveries'/('draft_'+digest(source)[:12]+'_'+key[:12])
        if (destination/'result.json').is_file():
            existing=read_json(destination/'result.json')
            if existing.get('booklet_complete') and existing.get('input_sha256')==digest(source):
                checked_draft(run,source)
                book=Path(existing['files']['pdf']).parent;stamp=read_json(book/'DONE.json')
                assert all((book/name).is_file() and digest(book/name)==expected for name,expected in stamp['files'].items())
                assert Path(existing['team_delivery']).is_file()
                print('复用已完成草稿图文；不重新规划、求解或渲染。',flush=True)
                return existing
        run.mkdir(parents=True,exist_ok=True)
        for name in (*DRAFT_FILES,'final_analysis.json','run_identity.json','source_steps.json','steps.csv','bom.csv'):
            if (origin/name).is_file():
                target=run/name
                if target.exists():assert digest(target)==digest(origin/name),'已有草稿快照不同，保留原件并停止'
                else:shutil.copy2(origin/name,target)
        if not (run/'model_steps.mpd').is_file():
            # Import and serialization only, never planning or stability analysis.
            from .compat.api import load_api
            from .rendering.framing import restore_plan
            api=load_api();loaded=api['import_ldraw'](source,ground=identity['ground'])
            assert loaded.load_complete and len(loaded.layout)==bom['brick_count']
            restored,_=restore_plan(plan);api['write_model'](loaded.layout,run/'model_steps.mpd',plan=restored)
        geometry=compare(source,run/'model_steps.mpd')
        write_json(run/'DRAFT_INPUT_VALIDATED.json',{'schema':'lego-graphics-only-input/v1','input_sha256':digest(source),
                   'source_checkpoint':str(origin),'package_version':'0.6.0','ground':identity['ground'],'config':identity['config'],
                   'pieces':bom['brick_count'],'steps':len(plan['steps']),'geometry_comparison':geometry,'plan_origin':identity.get('plan_origin','saved_planner_checkpoint'),
                   'step_verification_status':'INCOMPLETE','certification':None,
                   'files':{n:digest(run/n) for n in (*DRAFT_FILES,'final_analysis.json','run_identity.json','source_steps.json') if (run/n).is_file()}})
        checked_draft(run,source)
        write_json(run/'DRAFT_PROGRESS.json',{'status':'running','phase':'graphics_only_export','pieces':bom['brick_count'],
                   'steps':len(plan['steps']),'step_verification_status':'INCOMPLETE'})
        analysis_report(destination,run,title,plan,bom,identity,final)
        with (destination/'bom.csv').open('w',encoding='utf-8-sig',newline='') as f:
            fields=['part_key','ldraw_part','colour_code','colour_name','quantity','mass_g']
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(bom['total'])
        from argparse import Namespace
        from .rendering.pipeline import render_and_booklet
        args=Namespace(renderer=settings.renderer,ldraw_dir=settings.ldraw_dir,title=title,expected_sha256=digest(source))
        book=render_and_booklet(args,'custom',run,source,draft=True)
        result={'model':title,'input_sha256':digest(source),'export_mode':'graphics_only_draft','booklet_complete':True,
                'instruction_certified':False,'step_verification_status':'INCOMPLETE','status':'DRAFT_VALIDATION_INCOMPLETE',
                'files':{'pdf':str(book/'instructions_cn.pdf'),'html':str(book/'instructions_cn.html'),
                         'bom':str(destination/'bom.csv'),'model_steps':str(run/'model_steps.mpd'),
                         'stability_report':str(destination/'stability_report.html'),'stability_summary':str(destination/'stability_summary.json')},
                'team_delivery':str(destination/'Team_Delivery.zip')}
        write_json(destination/'result.json',result)
        from .cli import delivery_archive
        delivery_archive(destination,source,run,book)
        write_json(run/'DRAFT_PROGRESS.json',{'status':'completed','phase':'graphics_only_export','pieces':bom['brick_count'],
                   'steps':len(plan['steps']),'result':str(destination/'result.json'),'step_verification_status':'INCOMPLETE'})
        assert all(digest(origin/n)==h for n,h in originals.items()),'原始检查点发生变化'
        print('草稿图文导出完成；逐步骤静力仍为未完成验证：'+str(destination/'result.json'),flush=True)
        return result
