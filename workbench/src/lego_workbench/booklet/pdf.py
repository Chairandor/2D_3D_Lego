"""Chinese PDF/HTML from saved plans and real native-rendered images."""
import hashlib
import json
import math
import re
from pathlib import Path
from html import escape
from PIL import Image, ImageChops
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor

COLOURS = {'White':'白色','Black':'黑色','Red':'红色','Light_Bluish_Grey':'浅蓝灰色','Dark_Bluish_Grey':'深蓝灰色','Light_Gray':'浅灰色','Dark_Gray':'深灰色','Medium_Nougat':'中牛轧糖色','Dark_Orange':'深橙色','Orange':'橙色','Yellow':'黄色','Blue':'蓝色','Green':'绿色','Tan':'浅褐色','Dark_Tan':'深褐色','Brown':'棕色','Reddish_Brown':'红棕色','Pink':'粉色','Dark_Turquoise':'深青绿色','Light_Violet':'浅紫色','Lime':'黄绿色'}

def part_name(key):
    for prefix,name in [('slope_inverted_45_','45° 反斜面砖'),('slope_33_','33° 斜面砖'),('slope_45_','45° 斜面砖'),('brick_','砖'),('plate_','薄板'),('tile_','光面板')]:
        if key.startswith(prefix): return name+' '+key[len(prefix):].replace('x','×')
    return key

def thumb_name(p):
    part = str(p['ldraw_part'])
    if not re.fullmatch(r'[A-Za-z0-9_-]+', part): raise ValueError('非法零件编号')
    return f'{part}_{int(p["colour_code"])}.png'

def validate(plan, bom):
    n=len(plan['steps']);assert n>0
    assert [s['index'] for s in plan['steps']]==list(range(1,n+1))
    assert [s['step'] for s in bom['steps']]==list(range(1,n+1))
    ids=[i for s in plan['steps'] for i in s['brick_ids']]
    assert len(ids)==len(set(ids))==bom['brick_count']
    assert sum(p['quantity'] for p in bom['total'])==bom['brick_count']
    for s,b in zip(plan['steps'],bom['steps']):
        assert len(s['brick_ids'])==b['brick_count']==sum(p['quantity'] for p in b['parts'])
    expected={}
    for p in bom['total']:expected[(p['ldraw_part'],p['colour_code'])]=p['quantity']
    actual={}
    for s in bom['steps']:
        for p in s['parts']:
            k=p['ldraw_part'],p['colour_code'];actual[k]=actual.get(k,0)+p['quantity']
    assert expected==actual

def make(run, out, title, detail_steps,*,verification_incomplete=False,analysis_state=None):
    plan=json.loads((run/'plan.json').read_text(encoding='utf-8-sig'))
    bom=json.loads((run/'bom.json').read_text(encoding='utf-8-sig'))
    validate(plan,bom)
    steps=plan['steps'];count=len(steps);subs={s['name']:s for s in plan['subassemblies']}
    fragile={int(m.group(1)) for w in plan['warnings'] if (m:=re.match(r'^step (\d+): insertion-fragile',w))}
    fragile.update(s['index'] for s in steps if s.get('insertion_fragile'))
    unstable={s['index'] for s in steps if s['prefix_stable'] is False}
    state_label=None
    if analysis_state is not None:
        from ..bounded_analysis import LABELS
        state_label=LABELS[analysis_state['status']]
        unstable.update(analysis_state.get('static_risk_steps',[]))
        fragile.update(analysis_state.get('insertion_risk_steps',[]))
        checked={int(s['index']):s['stable'] for s in analysis_state.get('steps',[])}
        def step_notice(n):
            verdict='自重静力通过' if checked.get(n) is True else '自重静力未通过，需要支撑' if checked.get(n) is False else '本步骤未完成验证'
            risk='；保留已知静力风险' if n in unstable else '；保留已知插入风险' if n in fragile else ''
            return verdict+risk+'；不代表实物或插入过程认证。'
    totals=math.ceil(len(bom['total'])/28)
    total_pages=2+totals+count+len(detail_steps)
    font=Path(__file__).resolve().parents[3]/'assets'/'fonts'/'chinese.ttf'
    if 'LegoCN' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('LegoCN',str(font)))
    W,H=A4;M=42;INK='#18282f';MUTE='#54646d';ACC='#a45225'
    c=canvas.Canvas(str(out/'instructions_cn.pdf'),pagesize=A4,pageCompression=1)
    c.setTitle(title+' · 中文拼搭说明书');c.setAuthor('3D 乐高课程项目')
    pg=0; boxes={}
    def text(x,y,s,size=11,color=INK,limit=None):
        limit=(W-M-x) if limit is None else limit
        while pdfmetrics.stringWidth(s,'LegoCN',size)>limit and size>7:size-=.25
        if pdfmetrics.stringWidth(s,'LegoCN',size)>limit:raise ValueError('文字过长：'+s)
        c.setFillColor(HexColor(color));c.setFont('LegoCN',size);c.drawString(x,y,s)
    def rule(y):
        c.setStrokeColor(HexColor('#dce2e5'));c.setLineWidth(.5);c.line(M,y,W-M,y)
    def begin(kicker,heading,subtitle):
        nonlocal pg
        pg+=1;text(M,H-38,kicker,9,MUTE);text(M,H-74,heading,23)
        text(M,H-98,subtitle,10,MUTE);rule(H-115)
    def end():
        rule(44);text(M,27,title+(' · 静力：'+state_label+' · 草稿' if state_label else ' · 未完成验证草稿' if verification_incomplete else ' · 稳定性待验证'),9,MUTE)
        c.setFont('LegoCN',9);c.drawRightString(W-M,27,f'{pg} / {total_pages}');c.showPage()
    def picture(path,x,y,w,h):
        if path not in boxes:
            with Image.open(path) as raw:
                image=raw.convert('RGB');base=image.getpixel((0,0))
                diff=ImageChops.difference(image,Image.new('RGB',image.size,base))
                mask=diff.convert('L').point(lambda t:255 if t>5 else 0)
                box=mask.getbbox()
                if box is None:raise ValueError('空白步骤图：'+str(path))
                left,top,right,bottom=box;pad=max(8,int(max(right-left,bottom-top)*.05))
                box=max(0,left-pad),max(0,top-pad),min(image.width,right+pad),min(image.height,bottom+pad)
                boxes[path]=(image.width,image.height,box,base)
        iw,ih,(left,top,right,bottom),base=boxes[path]
        scale=min(w/(right-left),h/(bottom-top));vw,vh=(right-left)*scale,(bottom-top)*scale
        vx,vy=x+(w-vw)/2,y+(h-vh)/2
        c.saveState();c.setFillColorRGB(*(v/255 for v in base));c.rect(x,y,w,h,stroke=0,fill=1)
        clip=c.beginPath();clip.rect(vx,vy,vw,vh);c.clipPath(clip,stroke=0,fill=0)
        c.drawImage(str(path),vx-left*scale,vy-(ih-bottom)*scale,width=iw*scale,height=ih*scale)
        c.restoreState()
    def image(n):return out/'steps'/f'step_{n:04d}.png'
    def subname(s):return '子组件 '+s.split('-')[-1]
    begin('3D 乐高课程项目 / 图文说明书',title+'拼搭说明书',f'{bom["brick_count"]} 件零件 · {count} 步 · {len(subs)} 个子组件')
    picture(image(count),M,246,W-2*M,435)
    text(M,207,'中文阅读版 · 零件小图'+(' · 子组件装入对照' if subs else '')+' · 补充视角',14)
    text(M,177,'黄色边线标记本步新增内容；实际颜色以每步零件清单为准。',10)
    text(M,148,('稳定性：'+state_label+'；已知风险保留。插入受力与实物拼搭仍需验证。') if state_label else '逐步骤静力：未完成验证；已有风险提示保留，本说明书为待验证草稿。' if verification_incomplete else '稳定性优化暂缓；已有检查记录和提醒保留，说明书为待验证草稿。',10)
    text(M,120,'本说明书不代表实物拼搭全过程已通过稳定性认证。',10,MUTE);end()
    begin('阅读说明','怎样使用这本说明书','尺寸以凸点数表示；零件清单同时提供 LDraw 编号和颜色编号。')
    for j,s in enumerate(['按编号逐步阅读；先取零件，再按黄色边线定位新增内容。','子组件页：单独拼搭。装入页：查看主模型与子组件对照图。','转动模型提示：按图改变观察方向，不改变零件之间的连接。','补充视角页：观察同一步的另一侧；它不是额外的拼搭步骤。']):text(M,H-151-j*27,f'{j+1}. '+s,11)
    text(M,H-290,'子组件构建与装入位置',15)
    if subs:
        for j,sub in enumerate(subs.values()):
            ids=[s['index'] for s in steps if s['submodel']==sub['name']]
            attach=next(s['index'] for s in steps if s['attaches']==sub['name'])
            text(M,H-321-j*28,subname(sub['name']),11)
            text(M+142,H-321-j*28,f'第 {min(ids)}–{max(ids)} 步构建',11)
            text(M+320,H-321-j*28,f'第 {attach} 步装入 / {len(sub["brick_ids"])} 件',10)
    else:text(M,H-322,'本方案按主模型顺序逐步拼搭，不含单独构建的子组件。',11)
    text(M,247,'保留的计划风险提示' if verification_incomplete else '保留的数字检查记录',15)
    text(M,217,(f'计划静力风险提示 {len(unstable)} 步；计划插入风险提示 {len(fragile)} 步。' if verification_incomplete else f'中间静力风险 {len(unstable)} 步；按压插入风险提醒 {len(fragile)} 步。'),11)
    text(M,187,('当前静力分析：'+state_label+'；各步骤状态见步骤页和分析报告。') if state_label else '逐步骤静力未完成验证；未标记风险不代表该步骤通过。' if verification_incomplete else '相关步骤保留简短提醒；完整求解参数与来源映射见原始报告。',10,MUTE)
    text(M,158,'这些指标不能换算成实际倒塌概率；本次没有做稳定性优化。',10,MUTE)
    text(M,114,'图像取景放大以便阅读，各页缩放不同，不能用于比较实际模型尺寸。',10,MUTE);end()
    for q in range(totals):
        begin('准备零件',f'总零件清单 {q+1} / {totals}',f'共 {len(bom["total"])} 种零件与颜色组合，合计 {bom["brick_count"]} 件。')
        for x,s in [(M,'零件 / 尺寸'),(M+183,'颜色（编号）'),(M+335,'LDraw'),(M+453,'数量')]:text(x,H-145,s,9,MUTE)
        rule(H-155)
        for j,p in enumerate(bom['total'][q*28:(q+1)*28]):
            y=H-178-j*21
            text(M,y,part_name(p['part_key']),10,limit=177)
            text(M+183,y,COLOURS.get(p['colour_name'],p['colour_name'])+f' ({p["colour_code"]})',9,limit=147)
            text(M+335,y,p['ldraw_part'],10,limit=112);text(M+453,y,str(p['quantity']),10)
        end()
    main_previous={};previous=None
    for s in steps:
        if not s['submodel']:main_previous[s['index']]=previous;previous=s['index']
    for s,b in zip(steps,bom['steps']):
        n=s['index'];role='装入'+subname(s['attaches']) if s['attaches'] else '单独构建'+subname(s['submodel']) if s['submodel'] else '主模型'
        begin('拼搭步骤 / '+role,f'第 {n:02d} 步',role+(' · 转动模型，按图调整观察方向' if s['rotstep'] else ''))
        if s['attaches']:
            text(M,H-145,f'不取新零件；装入已完成的{subname(s["attaches"])}。',11)
            prev=main_previous[n];last=next(x['index'] for x in reversed(steps[:n-1]) if x['submodel']==s['attaches'])
            text(M,H-176,'装入前的主模型'+(f'（第 {prev} 步）' if prev else ''),10)
            text(M+265,H-176,f'已完成的子组件（第 {last} 步）',10)
            if prev:picture(image(prev),M,485,244,170)
            picture(image(last),M+265,485,244,170)
            text(M,463,'装入后：黄色边线标记本次装入的子组件。',10)
            picture(image(n),M,145,W-2*M,298)
        else:
            text(M,H-145,f'本步取 {b["brick_count"]} 件零件',12)
            for j,p in enumerate(b['parts']):
                x=M+(j%2)*265;y=H-191-(j//2)*45
                picture(out/'parts'/thumb_name(p),x,y-8,57,38)
                text(x+66,y+14,f'{p["quantity"]} 件  '+part_name(p['part_key']),10,limit=183)
                text(x+66,y-3,COLOURS.get(p['colour_name'],p['colour_name'])+f' · {p["ldraw_part"]}',9,MUTE,limit=183)
            rows=math.ceil(len(b['parts'])/2);top=H-176-max(1,rows)*45-20
            picture(image(n),M,145,W-2*M,min(380,top-145))
        text(M,126,('第 1 步：图中全部零件均为本步新增。' if n==1 else '黄色边线：本步新增内容。')+(' 本步另有补充视角，见下一页。' if n in detail_steps else ''),9,MUTE)
        if state_label:
            text(M,101,step_notice(n),10,ACC)
        elif verification_incomplete:
            notice='计划已标记静力风险，需支撑；本步骤未完成验证。' if n in unstable else '计划已标记按压插入风险；本步骤未完成验证。' if n in fragile else '本步骤未完成验证；图文仅供待验证草稿使用。'
            text(M,101,notice,10,ACC)
        elif n in unstable:text(M,101,'保留提醒：本步骤静力检查未通过，需要支撑；稳定性优化暂缓。',10,ACC)
        elif n in fragile:text(M,101,'保留提醒：本步骤存在按压插入风险，轻按并支撑连接处。',10,ACC)
        else:text(M,101,'完成本步后继续下一步。' if n<count else '图文流程结束；稳定性仍待后续完整验证。',10,MUTE)
        end()
        if n in detail_steps:
            begin('补充视角 / 不新增零件',f'第 {n:02d} 步 · 两侧观察','同一拼搭状态的两个观察方向；请先完成本步，再转动模型查看。')
            text(M,H-145,'原步骤视角',11);picture(image(n),M,423,W-2*M,265)
            text(M,397,'另一侧视角（水平转动 180°）',11)
            picture(out/'details'/f'step_{n:04d}_opposite.png',M,107,W-2*M,270)
            text(M,80,'两图展示同一步；原步骤图用于定位新增内容，另一侧用于观察结构。',10,MUTE);end()
    c.save();assert pg==total_pages
    cards=[]
    for s,b in zip(steps,bom['steps']):
        n=s['index'];role='装入'+subname(s['attaches']) if s['attaches'] else '单独构建'+subname(s['submodel']) if s['submodel'] else '主模型'
        parts=''.join(f'<li><img class="part" src="parts/{escape(thumb_name(p))}">{p["quantity"]} 件 {escape(part_name(p["part_key"]))} · {escape(COLOURS.get(p["colour_name"],p["colour_name"]))} · {escape(p["ldraw_part"])}</li>' for p in b['parts'])
        extra=f'<h3>另一侧视角：同一步，不新增零件</h3><img class="scene" src="details/step_{n:04d}_opposite.png">' if n in detail_steps else ''
        attach=''
        if s['attaches']:
            prev=main_previous[n];last=next(x['index'] for x in reversed(steps[:n-1]) if x['submodel']==s['attaches'])
            attach=f'<p>装入已完成的{subname(s["attaches"])}，不取新零件。</p><div class="pair">'+(f'<figure><img src="steps/step_{prev:04d}.png"><figcaption>装入前的主模型</figcaption></figure>' if prev else '')+f'<figure><img src="steps/step_{last:04d}.png"><figcaption>已完成的子组件</figcaption></figure></div>'
        warning='本步骤静力检查未通过，需要支撑；稳定性优化暂缓。' if n in unstable else '本步骤存在按压插入风险，轻按并支撑连接处。' if n in fragile else ''
        if verification_incomplete:warning=('计划已标记静力风险，需支撑。' if n in unstable else '计划已标记插入风险。' if n in fragile else '')+'本步骤未完成验证；未标记风险不代表通过。'
        if state_label:warning=step_notice(n)
        cards.append(f'<section id="step-{n}"><h2>第 {n:02d} 步 · {role}</h2><p>{"转动模型，按图调整观察方向。" if s["rotstep"] else ""}</p><ul>{parts}</ul>{attach}<img class="scene" src="steps/step_{n:04d}.png"><p class="warning">{warning}</p>{extra}</section>')
    rows=''.join(f'<tr><td>{escape(part_name(p["part_key"]))}</td><td>{escape(COLOURS.get(p["colour_name"],p["colour_name"]))}</td><td>{escape(p["ldraw_part"])}</td><td>{p["quantity"]}</td></tr>' for p in bom['total'])
    html=f'<!doctype html><meta charset="utf-8"><title>{escape(title)}拼搭说明书</title><style>body{{font:16px system-ui;max-width:1000px;margin:30px auto;padding:20px;line-height:1.7}}section{{margin:50px 0;border-top:1px solid #ddd;padding-top:20px}}.scene{{width:100%}}.part{{width:70px;vertical-align:middle;margin-right:12px}}ul{{list-style:none;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:12px}}.pair{{display:flex}}.pair figure{{width:50%;margin:10px}}.pair img{{width:100%}}table{{width:100%;border-collapse:collapse}}td,th{{padding:6px;text-align:left;border-bottom:1px solid #ddd}}.warning{{color:#a45225}}</style><h1>{escape(title)}拼搭说明书</h1><p>{bom["brick_count"]} 件 · {count} 步 · 稳定性待验证。</p><p>黄色边线标记本步新增内容；子组件装入对照与补充视角不新增零件。</p><h2>总零件清单</h2><table><tr><th>零件</th><th>颜色</th><th>LDraw 编号</th><th>数量</th></tr>{rows}</table>'+''.join(cards)
    if verification_incomplete:html=html.replace('<h2>总零件清单</h2>','<p class="warning">逐步骤静力：未完成验证。保留计划风险提示，没有生成认证结果；说明书为待验证草稿。</p><h2>总零件清单</h2>')
    if state_label:
        html=html.replace('稳定性待验证。','稳定性：'+state_label+'。')
        html=html.replace('逐步骤静力：未完成验证。保留计划风险提示，没有生成认证结果；说明书为待验证草稿。','静力分析：'+state_label+'。仅更新文字并复用原图；插入过程和实物稳定性未认证。')
    (out/'instructions_cn.html').write_text(html,encoding='utf-8')
    result={'pdf_pages':total_pages,'steps':count,'pieces':bom['brick_count'],'detail_steps':detail_steps,'static_risk_steps':sorted(unstable),'insertion_risk_steps':sorted(fragile),'stability_optimization':False}
    result['step_verification_status']='INCOMPLETE' if verification_incomplete else 'COMPLETED'
    if state_label:
        result.update(stability_status=analysis_state['status'],stability_label=state_label,
                      step_verification_status='COMPLETED' if analysis_state['status'] in ('PASSED','FAILED') else 'INCOMPLETE')
    (out/'booklet_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
