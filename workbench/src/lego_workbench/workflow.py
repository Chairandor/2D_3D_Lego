"""Booklets first; independent, bounded static analysis afterwards."""
from argparse import Namespace
from html import escape
from pathlib import Path
import os
import re
import shutil
import tempfile
import zipfile
from .config import PROJECT
from .storage import digest, read_json, write_json, model_lock
from .bounded_analysis import LABELS

BANNED={'dbf917b222136797636177c8ce1a104581e050f962f94c6b04ccc1b33790d056',
        '3c44a2fb35c4b0439efd5ef61fd8f6da95a5ed756ae21c1fc8231ae59782c8cf',
        '6e9448d23dacb63b274030e13e4369fb94c924752fd06c4b8847cb22de89a73e'}

def show(result):
    print('说明书已生成，稳定性待分析' if result.get('stability_status')=='PENDING'
          else '说明书已生成；稳定性：'+LABELS[result['stability_status']],flush=True)
    for name in ('pdf','html'):print(name.upper()+'：'+str(result['files'][name]),flush=True)

def check_booklet(book,plan,bom):
    from .booklet.pdf import validate
    from .plan_coverage import validate_nonblank_steps
    from .rendering.pipeline import content_extent
    from pypdf import PdfReader
    validate(plan,bom);validate_nonblank_steps(plan)
    assert (book/'instructions_cn.pdf').is_file() and (book/'instructions_cn.html').is_file()
    pages=len(PdfReader(book/'instructions_cn.pdf').pages)
    html=(book/'instructions_cn.html').read_text(encoding='utf-8')
    for src in re.findall(r'src="([^"]+)"',html):
        assert (book/src).is_file(),'HTML 缺图：'+src
    for step in plan['steps']:
        assert content_extent(book/'steps'/f'step_{step["index"]:04d}.png')>0
    return {'pieces':bom['brick_count'],'steps':len(plan['steps']),'pdf_pages':pages,'coverage':'PASSED','images':'PASSED','pdf_open':'PASSED'}

def cached(settings,source):
    """Portable audited team cache; originals are immutable, including Fox 8."""
    sha=digest(source)
    for root in (settings.output_root,*settings.reuse_roots):
        for path in root.glob('instructions_first_'+sha[:12]+'_*/workflow_result.json'):
            row=read_json(path)
            if row.get('input_sha256')==sha and row.get('stability_status') in ('PASSED','FAILED','TIMEOUT','ERROR'):
                from .draft import checked_draft
                _,plan,bom=checked_draft(path.parent,source)
                book=Path(row['files']['pdf']).parent;stamp=read_json(book/'DONE.json')
                assert all(digest(book/n)==h for n,h in stamp['files'].items())
                check_booklet(book,plan,bom)
                return dict(row,booklet_reused=True)
    index=PROJECT/'checkpoints'/'team_index.json'
    if index.is_file():
        for row in read_json(index)['models']:
            if row['input_sha256']!=sha:continue
            for name,expected in row['hashes'].items():
                assert digest(PROJECT/name)==expected,'团队成品缓存改变：'+name
            result=dict(row)
            result['files']={k:str((PROJECT/v).resolve()) for k,v in row['files'].items()}
            result['booklet_reused']=True
            return result
    for root in (settings.output_root,*settings.reuse_roots):
        for path in (root/'deliveries').glob('*/result.json'):
            try:
                row=read_json(path)
                if row.get('input_sha256')!=sha or not row.get('booklet_complete'):continue
                book=Path(row['files']['pdf']).parent;stamp=read_json(book/'DONE.json')
                assert stamp['files'] and all(digest(book/n)==h for n,h in stamp['files'].items())
                summary=read_json(row['files']['stability_summary'])
                plan=read_json(Path(summary['source_run'])/'plan.json')
                bom=read_json(Path(summary['source_run'])/'bom.json')
                checks=check_booklet(book,plan,bom)
                incomplete=summary.get('step_verification_status')=='INCOMPLETE'
                # Unknown is not evidence of a timeout. Audited historical
                # timeouts live in team_index; an arbitrary pending draft must
                # proceed via its saved plan and actual source-wide ledger.
                if incomplete:continue
                status='PASSED' if summary['instruction_certified'] else 'FAILED'
                return dict(row,stability_status=status,booklet_reused=True,checks=checks,
                            reuse_note='历史完成成品；保留原报告和风险，不自动重试旧分析')
            except (OSError,ValueError,KeyError):continue
    return None

def state_report(run,analysis):
    plan=read_json(run/'plan.json');bom=read_json(run/'bom.json')
    result=dict(analysis)
    known={s['index'] for s in plan['steps'] if s['prefix_stable'] is False}
    steps=[];final=None
    folder=Path(analysis['checkpoint']) if analysis.get('checkpoint') else None
    if folder:
        for s in plan['steps']:
            path=folder/f'step_{s["index"]:04d}.json'
            if path.is_file():
                row=read_json(path)['final_stability']
                if row['status'] in ('optimal','optimal_inaccurate'):
                    steps.append({'index':s['index'],'stable':row['stable']})
                    if not row['stable']:known.add(s['index'])
        if (folder/'final_analysis.json').is_file():final=read_json(folder/'final_analysis.json')
    insertion={s['index'] for s in plan['steps'] if s.get('insertion_fragile')}
    insertion.update(int(m.group(1)) for w in plan.get('warnings',[]) if (m:=re.match(r'^step (\d+): insertion-fragile',w)))
    result.update(steps=steps,static_risk_steps=sorted(known),
        insertion_risk_steps=sorted(insertion),unverified_steps=[s['index'] for s in plan['steps'] if s['index'] not in {r['index'] for r in steps}],
        pieces=bom['brick_count'],step_count=len(plan['steps']),final_analysis=final,
        instruction_certified=False,meaning='自重静力数字分析，不代表插入受力或实物稳定认证；未修改模型。')
    write_json(run/'stability_summary.json',result)
    rows=''.join(f'<tr><td>{s["index"]}</td><td>{"通过" if s["stable"] else "未通过"}</td></tr>' for s in steps)
    text=f'<!doctype html><meta charset="utf-8"><h1>稳定性：{LABELS[result["status"]]}</h1><p>{escape(result["meaning"])}</p><p>累计耗时：{result.get("used_seconds",0):.3f} 秒；上限 {result.get("limit_seconds",1800)} 秒；已完成 {len(steps)}/{len(plan["steps"])} 个步骤。</p><p>说明书生成成功与稳定性分别记录；已保存的风险保留。未列出的步骤尚未验证。</p><p>{escape(str(result.get("error","")))}</p><table><tr><th>步骤</th><th>自重静力</th></tr>{rows}</table><p>完整最终模型/各步骤数值、连通性及地面假设见 JSON 检查点。StableLego/Gurobi 记录独立，没有重跑。</p>'
    (run/'stability_report.html').write_text(text,encoding='utf-8')
    return result

def refresh_text(run,book,title,state):
    """Uses existing images only; never imports/plans/renders/solves."""
    from .booklet.pdf import make
    meta=read_json(book/'booklet_summary.json')
    images={p:digest(p) for group in ('steps','parts','details') for p in (book/group).glob('*.png')}
    # Author into a separate directory so an open PDF is never truncated. On
    # Windows a viewer may deny replacement; keep its file and publish a clearly
    # named updated PDF alongside it instead.
    pdf_name='instructions_cn.pdf'
    with tempfile.TemporaryDirectory(prefix='status_text_',dir=run) as temp:
        staged=Path(temp)
        for group in ('steps','parts','details'):
            (staged/group).mkdir()
            for p in (book/group).glob('*.png'):
                try:os.link(p,staged/group/p.name)
                except OSError:shutil.copy2(p,staged/group/p.name)
        make(run,staged,title,meta['detail_steps'],verification_incomplete=True,analysis_state=state)
        try:os.replace(staged/'instructions_cn.pdf',book/pdf_name)
        except PermissionError:
            pdf_name='instructions_cn_status.pdf'
            os.replace(staged/'instructions_cn.pdf',book/pdf_name)
            print('查看器占用原 PDF；状态更新版：'+str(book/pdf_name),flush=True)
        for name in ('instructions_cn.html','booklet_summary.json'):os.replace(staged/name,book/name)
    assert all(digest(p)==h for p,h in images.items())
    stamp=read_json(book/'DONE.json')
    stamp['files']={n:digest(book/n) for n in ('instructions_cn.pdf','instructions_cn.html','booklet_summary.json')}
    if pdf_name!='instructions_cn.pdf':stamp['files'][pdf_name]=digest(book/pdf_name)
    write_json(book/'DONE.json',stamp)
    return book/pdf_name

def archive_result(run,source,book,result):
    target=run/'Result.zip';temporary=run/'Result.zip.tmp'
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(source,'input/'+source.name)
        for name in ('plan.json','bom.json','bom.csv','source_map.json','model_steps.mpd','run_identity.json',
                     'DRAFT_INPUT_VALIDATED.json','stability_summary.json','stability_report.html','workflow_result.json'):
            if (run/name).is_file():archive.write(run/name,'analysis_and_steps/'+name)
        checkpoint=result.get('analysis',{}).get('checkpoint')
        if checkpoint:
            for path in Path(checkpoint).glob('*.json'):archive.write(path,'static_checkpoints/'+path.name)
        for path in book.rglob('*'):
            if path.is_file() and path.suffix.lower() in ('.pdf','.html','.png','.json'):
                archive.write(path,'booklet/'+path.relative_to(book).as_posix())
    os.replace(temporary,target)
    return target

def process(settings,source,title=None,*,analysis_seconds=1800,plan_run=None):
    from .planning import prepare
    from .rendering.pipeline import render_and_booklet
    from .bounded_analysis import run as analyze
    source=Path(source).resolve();title=title or source.stem
    if not 0 < analysis_seconds <= 1800:raise ValueError('累计稳定性时限必须在 0–1800 秒之间')
    if not source.is_file() or source.suffix.lower() not in ('.mpd','.ldr'):raise ValueError('需要存在的 .ldr 或 .mpd 输入')
    sha=digest(source)
    if sha in BANNED or '7ken' in {p.casefold() for p in source.parts}:
        raise ValueError('此输入属于已约定排除的旧实验/7ken；不会运行')
    with model_lock(settings.output_root,sha):
        old=cached(settings,source)
        if old:
            show(old);print('复用已完成说明书、图片和已有分析；保留历史未完成状态。',flush=True)
            return old
        if plan_run is None:
            # Older interrupted runs may have a fully saved plan but no booklet.
            # Audit the original marker before using the explicit draft path.
            from .draft import audit_plan
            for root in (settings.output_root,*settings.reuse_roots):
                for identity_path in root.glob('*/run_identity.json'):
                    candidate=identity_path.parent
                    if candidate.name.startswith('instructions_first_'):continue
                    identity=read_json(identity_path)
                    if identity.get('source_sha256')!=sha:continue
                    if (candidate/'plan.json').is_file() and (candidate/'bom.json').is_file():
                        audit_plan(candidate,source);plan_run=candidate;break
                if plan_run is not None:break
        run=prepare(settings,source) if plan_run is None else Path(plan_run).resolve()
        if plan_run:
            # Existing checkpoints must use the explicit draft path, not forged
            # completed verification markers. All original files stay immutable.
            from .draft import export_draft
            draft=export_draft(settings,source,run,title)
            book=Path(draft['files']['pdf']).parent;run=book.parent
        else:
            args=Namespace(renderer=settings.renderer,ldraw_dir=settings.ldraw_dir,title=title,expected_sha256=sha,
                           analysis_state={'status':'PENDING'})
            book=render_and_booklet(args,'custom',run,source,draft=True)
        plan=read_json(run/'plan.json');bom=read_json(run/'bom.json')
        checks=check_booklet(book,plan,bom)
        result={'model':title,'input_sha256':sha,'booklet_complete':True,'booklet_reused':False,
                'generation_status':'PASSED','instruction_certified':False,'stability_status':'PENDING','checks':checks,
                'files':{'pdf':str(book/'instructions_cn.pdf'),'html':str(book/'instructions_cn.html'),
                         'bom':str(run/'bom.csv'),'model_steps':str(run/'model_steps.mpd'),
                         'stability_summary':str(run/'stability_summary.json'),'stability_report':str(run/'stability_report.html')}}
        state_report(run,{'status':'PENDING'})
        result['team_delivery']=str(run/'Result.zip')
        write_json(run/'workflow_result.json',result)
        archive_result(run,source,book,result)
        show(result)  # Flushed before even creating the analysis child process.
        print('开始后续稳定性分析；每模型累计预算最多 30 分钟，说明书现在即可打开。',flush=True)
        analysis=analyze(settings,source,run,limit_seconds=analysis_seconds)
        state=state_report(run,analysis)
        # A completed run repeats only text if its analysis outcome actually changed.
        current=read_json(book/'booklet_summary.json').get('stability_status')
        if current!=state['status']:result['files']['pdf']=str(refresh_text(run,book,title,state))
        result.update(stability_status=state['status'],checks=check_booklet(book,plan,bom),analysis=state)
        write_json(run/'workflow_result.json',result)
        archive_result(run,source,book,result)
        assert digest(source)==sha,'输入文件运行期间改变'
        print('说明书保持可用；稳定性：'+LABELS[state['status']],flush=True)
        print('分析报告：'+result['files']['stability_report'],flush=True)
        return result
