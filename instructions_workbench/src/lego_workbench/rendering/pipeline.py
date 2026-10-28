"""Native rendering and existing-plan booklets, reused from display patch v1.1."""
import contextlib
import hashlib
import io
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageChops
from .framing import FramedRunner, digest, dump, png_ok, restore_plan, sanitize, sections, step_views
from ..booklet.pdf import make, thumb_name, validate
from ..engine import checked_run as check_run
from ..storage import CORE_FILES
WIDTH, HEIGHT = 1800, 1350
VERSION = 'booklet-detail-v1.1'
def prefix_scene(model_text, plan, index, opposite=True):
    """Native MPD frame prefix, including its existing child FILE sections."""
    local=sections(sanitize(model_text),'model_steps.mpd')
    sub,ordinal,longitude=step_views(plan)[index]
    name=next(iter(local)) if sub is None else next((n for n in local if n.endswith('-'+sub+'.ldr')),None)
    if name is None:raise ValueError('找不到步骤所对应的子模型')
    body=[];boundaries=0
    for line in local[name]:
        body.append(line)
        if line.strip().upper()=='0 STEP':
            boundaries+=1
            if boundaries==ordinal:break
    if boundaries!=ordinal:raise ValueError('模型步骤边界与计划不一致')
    lines=['0 FILE '+name,*body,'0 NOFILE']
    for other,content in local.items():
        if other!=name:lines.extend(['0 FILE '+other,*content,'0 NOFILE'])
    return '\n'.join(lines)+'\n',ordinal,(longitude+180)%360 if opposite else longitude

def select_details(plan, limit=12):
    chosen=[]
    priorities=[s.index for s in plan.steps if s.attaches]+[plan.steps[-1].index]+[s.index for s in plan.steps if s.rotstep]+[s.index for s in plan.steps if s.prefix_stable is False]
    for n in priorities:
        if n not in chosen:chosen.append(n)
        if len(chosen)>=limit:break
    return sorted(chosen)

def inherit_images(run, out, original, args):
    copied=0
    for version in ('booklet-detail-v1.1','booklet-detail-v1'):
        oldfp=hashlib.sha256(json.dumps([version,original,WIDTH,HEIGHT,str(args.renderer.resolve()),str(args.ldraw_dir.resolve())],sort_keys=True).encode()).hexdigest()
        old=run/('booklet_detail_v1_'+oldfp[:12])
        if not (old/'DONE.json').is_file():continue
        try:status=json.loads((old/'DONE.json').read_text())
        except (ValueError,OSError):continue
        if status.get('fingerprint')!=oldfp:continue
        for group,dims in [('steps',(WIDTH,HEIGHT)),('parts',(400,300)),('details',(WIDTH,HEIGHT))]:
            for source in (old/group).glob('*.png'):
                dest=out/group/source.name
                if not png_ok(dest,*dims) and png_ok(source,*dims):shutil.copyfile(source,dest);copied+=1
        for name in ('render_report.json','refinement.json'):
            if (old/name).is_file() and not (out/name).is_file():
                shutil.copyfile(old/name,out/name)
    if copied:print(f'复用上一版原生图片 {copied} 张；原说明书保留。',flush=True)

def content_extent(path):
    with Image.open(path) as raw:
        image=raw.convert('RGB');base=image.getpixel((0,0))
        diff=ImageChops.difference(image,Image.new('RGB',image.size,base)).convert('L').point(lambda t:255 if t>5 else 0)
        box=diff.getbbox()
        if box is None:raise ValueError('空白步骤图片')
        return max(box[2]-box[0],box[3]-box[1])

def inherit_part_images(run,out,args,bom):
    """Reuse identical native part/colour thumbnails from completed matching renders."""
    copied=0
    for result_path in (run.parent/'deliveries').glob('*/result.json'):
        try:
            result=json.loads(result_path.read_text(encoding='utf-8-sig'))
            if not result.get('booklet_complete'):continue
            old=Path(result['files']['pdf']).parent
            if old.resolve()==out.resolve():continue
            summary=json.loads(Path(result['files']['stability_summary']).read_text(encoding='utf-8-sig'))
            oldrun=Path(summary['source_run'])
            if result.get('export_mode')=='graphics_only_draft':
                from ..draft import DRAFT_FILES
                original={n:digest(oldrun/n) for n in (*DRAFT_FILES,'DRAFT_INPUT_VALIDATED.json')}
            else:original={n:digest(oldrun/n) for n in CORE_FILES}
            fp=hashlib.sha256(json.dumps([VERSION,original,WIDTH,HEIGHT,str(args.renderer.resolve()),str(args.ldraw_dir.resolve()),result['model']],sort_keys=True).encode()).hexdigest()
            stamp=json.loads((old/'DONE.json').read_text(encoding='utf-8-sig'))
            if stamp.get('fingerprint')!=fp:continue
            if not all((old/n).is_file() and digest(old/n)==h for n,h in stamp['files'].items()):continue
            for part in bom['total']:
                source=old/'parts'/thumb_name(part);dest=out/'parts'/thumb_name(part)
                if not png_ok(dest,400,300) and png_ok(source,400,300):
                    shutil.copyfile(source,dest);copied+=1
        except (OSError,ValueError,KeyError):
            continue
    if copied:print(f'复用其他已完成模型的相同零件/颜色原生小图 {copied} 张；来源成品保留。',flush=True)
    return copied

def refine_early_views(runner,args,run,out,plan):
    state=out/'refinement.json'
    finished=set(json.loads(state.read_text()).get('steps',[])) if state.is_file() else set()
    pending=[s.index for s in plan.steps if s.index not in finished and content_extent(out/'steps'/f'step_{s.index:04d}.png')<500]
    if not pending:return sorted(finished)
    text=(run/'model_steps.mpd').read_text(encoding='utf-8-sig');scene=out/'native_refinement_scene.mpd'
    print(f'仅补渲染 {len(pending)} 张清晰度不足的步骤图；步骤与数字报告不变。',flush=True)
    try:
        for i,n in enumerate(pending,1):
            content,ordinal,longitude=prefix_scene(text,plan,n,opposite=False)
            scene.write_text(content,encoding='utf-8');dest=out/'steps'/f'step_{n:04d}.png'
            invoke_one(runner,args,scene,dest,WIDTH,HEIGHT,ordinal,longitude,True)
            finished.add(n);dump(state,{'steps':sorted(finished),'method':'native prefix camera; original view angle unchanged'})
            print(f'局部取景 {i}/{len(pending)}（第 {n} 步）',flush=True)
    finally:scene.unlink(missing_ok=True)
    return sorted(finished)

def invoke_one(runner, args, scene, dest, width, height, ordinal=1, longitude=45, highlight=False):
    cmd=[str(args.renderer.resolve()),str(scene),'-i',str(dest),'-w',str(width),'-h',str(height),'--camera-angles','30',str(longitude),'-f',str(ordinal),'-t',str(ordinal),'-l',str(args.ldraw_dir.resolve())]
    if highlight:cmd.append('--highlight')
    with contextlib.redirect_stdout(io.StringIO()):runner(cmd,240.0)

def export_review(run,out):
    archive=out/'Review_For_Chat.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for n in (*CORE_FILES,'READY_FOR_RENDER','steps.csv','error.json'):
            if (run/n).is_file():z.write(run/n,n)
        for n in ('booklet_summary.json','render_report.json','refinement.json','error.json','DONE.json'):
            if (out/n).is_file():z.write(out/n,'booklet/'+n)
    return archive

def render_and_booklet(args,key,run,source,*,draft=False):
    if draft:
        from ..draft import checked_draft,DRAFT_FILES
        report,payload,bom=checked_draft(run,source)
        core_files=(*DRAFT_FILES,'DRAFT_INPUT_VALIDATED.json')
    else:report,payload,bom=check_run(run,source);core_files=CORE_FILES
    label=args.title
    original={n:digest(run/n) for n in core_files}
    plan,repaired=restore_plan(payload)
    fp=hashlib.sha256(json.dumps([VERSION,original,WIDTH,HEIGHT,str(args.renderer.resolve()),str(args.ldraw_dir.resolve()),label],sort_keys=True).encode()).hexdigest()
    out=run/('booklet_detail_v1_'+fp[:12]);out.mkdir(exist_ok=True)
    for name in ('steps','parts','details'): (out/name).mkdir(exist_ok=True)
    inherit_images(run,out,original,args)
    details=select_details(plan)
    done=out/'DONE.json'
    if done.is_file():
        status=json.loads(done.read_text())
        all_pngs=all(png_ok(out/'steps'/f'step_{i:04d}.png',WIDTH,HEIGHT) for i in range(1,len(plan.steps)+1)) and all(png_ok(out/'parts'/thumb_name(p),400,300) for p in bom['total']) and all(png_ok(out/'details'/f'step_{n:04d}_opposite.png',WIDTH,HEIGHT) for n in details)
        if all_pngs and status.get('fingerprint')==fp and all((out/n).is_file() and digest(out/n)==h for n,h in status.get('files',{}).items()) and set(status.get('files',{}))=={'instructions_cn.pdf','instructions_cn.html','booklet_summary.json'}:
            print(label+'：完整说明书已经生成，直接复用 '+str(out/'instructions_cn.pdf'),flush=True);return out
    runner=FramedRunner(args.ldraw_dir,run/'detail_render_cache')
    from legolization.instructions import RenderConfig, Renderer, render_step_images
    count=len(plan.steps);existing=sum(png_ok(out/'steps'/f'step_{i:04d}.png',WIDTH,HEIGHT) for i in range(1,count+1))
    if existing<count:
        print(f'{label} [5/6] 输出高清步骤图；已保存 {existing}/{count}，缓存会自动复用。',flush=True)
        images=render_step_images(run/'model_steps.mpd',plan,config=RenderConfig(width=WIDTH,height=HEIGHT,renderer=Renderer(kind='leocad',executable=args.renderer.resolve()),ldraw_dir=args.ldraw_dir.resolve()),runner=runner,progress=lambda e:print(str(e),flush=True))
        for i,data in enumerate(images.images,1):
            if data is not None:(out/'steps'/f'step_{i:04d}.png').write_bytes(data)
        dump(out/'render_report.json',{'rendered_steps':sum(x is not None for x in images.images),'total_steps':count,'all_images_present':images.complete,'warnings':list(images.warnings),'restored_warning_flags':repaired,'instruction_certified':bool(report['instruction_certified']),'physical_report_reused':True,'stability_optimization':False})
        if not images.complete:raise RuntimeError('步骤图尚未全部生成；已保存图片和缓存，重新运行可补缺。')
    refined=refine_early_views(runner,args,run,out,plan)
    print(f'{label} [5/6] 生成真实零件小图与子组件/旋转步骤补充视角。',flush=True)
    inherited_parts=inherit_part_images(run,out,args,bom)
    scene=out/'native_preview_scene.mpd'
    try:
        for i,p in enumerate(bom['total'],1):
            dest=out/'parts'/thumb_name(p)
            if not png_ok(dest,400,300):
                part=str(p['ldraw_part']);part=part if part.lower().endswith('.dat') else part+'.dat'
                scene.write_text(f'0 FILE part-preview.ldr\n1 {p["colour_code"]} 0 0 0 1 0 0 0 1 0 0 0 1 {part}\n0 STEP\n0 NOFILE\n',encoding='utf-8')
                invoke_one(runner,args,scene,dest,400,300)
            if i%5==0 or i==len(bom['total']):print(f'零件小图 {i}/{len(bom["total"])}',flush=True)
        model_text=(run/'model_steps.mpd').read_text(encoding='utf-8-sig')
        for i,n in enumerate(details,1):
            dest=out/'details'/f'step_{n:04d}_opposite.png'
            if not png_ok(dest,WIDTH,HEIGHT):
                content,ordinal,longitude=prefix_scene(model_text,plan,n);scene.write_text(content,encoding='utf-8')
                invoke_one(runner,args,scene,dest,WIDTH,HEIGHT,ordinal,longitude,True)
            print(f'补充视角 {i}/{len(details)}（第 {n} 步）',flush=True)
    finally:scene.unlink(missing_ok=True)
    print(label+' [6/6] 导出中文 PDF、HTML 和检查包。',flush=True)
    result=make(run,out,label,details,verification_incomplete=draft,analysis_state=getattr(args,'analysis_state',None))
    result['framing_refined_steps']=refined
    result['inherited_part_images']=inherited_parts
    dump(out/'booklet_summary.json',result)
    if any(digest(run/n)!=h for n,h in original.items()) or digest(source)!=args.expected_sha256:raise RuntimeError('原模型或步骤结果发生变化')
    dump(done,{'fingerprint':fp,'files':{n:digest(out/n) for n in ('instructions_cn.pdf','instructions_cn.html','booklet_summary.json')},'completed':datetime.now().isoformat()})
    export_review(run,out)
    print(f'完成：{count}/{count} 步；{result["pdf_pages"]} 页；PDF：{out/"instructions_cn.pdf"}',flush=True)
    return out
