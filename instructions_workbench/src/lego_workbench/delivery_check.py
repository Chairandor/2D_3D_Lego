"""Read-only checks. This module never imports legolization or invokes a renderer."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import zipfile
from PIL import Image, ImageChops
from pypdf import PdfReader
try:
    from .ldraw_geometry import compare
    from .plan_coverage import validate_nonblank_steps
except ImportError:
    from ldraw_geometry import compare
    from plan_coverage import validate_nonblank_steps

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))
def path_at(value,base):
    p=Path(value); return p if p.is_absolute() else (base/p).resolve()

def check(source,result_path):
    result=read(result_path); base=result_path.parent
    assert result['input_sha256']==sha(source),'输入哈希不符'
    files={k:path_at(v,base) for k,v in result['files'].items()}
    assert all(p.is_file() for p in files.values()),'缺少输出文件'
    summary=read(files['stability_summary'])
    run=path_at(summary['source_run'],files['stability_summary'].parent)
    plan=read(run/'plan.json'); bom=read(run/'bom.json')
    draft=result.get('export_mode')=='graphics_only_draft'
    if draft:
        meta=read(run/'DRAFT_INPUT_VALIDATED.json')
        assert meta['schema']=='lego-graphics-only-input/v1' and meta['input_sha256']==sha(source)
        assert summary['step_verification_status']=='INCOMPLETE' and summary['certification'] is None
        assert not result['instruction_certified'] and not summary['instruction_certified']
        assert not any((run/name).exists() for name in ('report.json','CORE_READY.json','VERIFY_READY.json','READY_FOR_RENDER'))
        for name,expected in meta['files'].items():assert sha(run/name)==expected
        report={'input_sha256':meta['input_sha256'],'brick_count':meta['pieces'],'step_count':meta['steps']}
    else:report=read(run/'report.json')
    geometry_comparison=compare(source,run/'model_steps.mpd')
    assert report['input_sha256']==sha(source)
    ids=[i for s in plan['steps'] for i in s['brick_ids']]
    assert plan['steps'],'没有步骤'
    validate_nonblank_steps(plan)
    assert len(ids)==len(set(ids))==bom['brick_count']==report['brick_count']
    assert sum(p['quantity'] for p in bom['total'])==report['brick_count']
    assert len(plan['steps'])==report['step_count']==len(bom['steps'])
    assert [s['index'] for s in plan['steps']]==list(range(1,len(plan['steps'])+1))
    counts=Counter()
    for step,parts in zip(plan['steps'],bom['steps']):
        assert len(step['brick_ids'])==parts['brick_count']==sum(p['quantity'] for p in parts['parts'])
        for p in parts['parts']: counts[p['ldraw_part'],p['colour_code']]+=p['quantity']
    assert counts==Counter({(p['ldraw_part'],p['colour_code']):p['quantity'] for p in bom['total']})
    book=files['pdf'].parent
    book_info=read(book/'booklet_summary.json')
    image_metrics=[]
    for group in ('steps','parts','details'):
        for path in sorted((book/group).glob('*.png')):
            with Image.open(path) as original:
                image=original.convert('RGB'); image.load()
                expected=(400,300) if group=='parts' else (1800,1350)
                assert image.size==expected,(path,image.size)
                diff=ImageChops.difference(image,Image.new('RGB',image.size,image.getpixel((0,0))))
                box=diff.convert('L').point(lambda t:255 if t>5 else 0).getbbox()
                assert box is not None,'空白图：'+str(path)
                assert box[0]>0 and box[1]>0 and box[2]<image.width and box[3]<image.height,'模型触及图片边界：'+str(path)
                image_metrics.append({'file':str(path.relative_to(book)),'bbox':box})
    assert len(list((book/'steps').glob('step_*.png')))==len(plan['steps'])
    assert len(list((book/'parts').glob('*.png')))==len(bom['total'])
    assert len(list((book/'details').glob('*.png')))==len(book_info['detail_steps'])
    references=re.findall(r'<img[^>]+src=[\"\x27]([^\"\x27]+)',files['html'].read_text(encoding='utf-8'))
    assert references and all((book/name).is_file() for name in references)
    pdf=PdfReader(files['pdf']); texts=[p.extract_text() or '' for p in pdf.pages]
    assert len(pdf.pages)==book_info['pdf_pages']
    assert all(t.strip() and '\ufffd' not in t for t in texts),'PDF 空页或替换字符'
    assert '拼搭说明书' in texts[0] and '待验证' in ''.join(texts)
    if draft:assert '未完成验证' in ''.join(texts) and '未完成验证' in files['html'].read_text(encoding='utf-8')
    # Embedded Chinese font is required, regardless of the local viewer's fonts.
    font_descriptors=[]
    for page in pdf.pages:
        resources=page.get('/Resources',{}).get_object()
        fonts=resources.get('/Font',{}).get_object()
        for ref in fonts.values():
            font=ref.get_object(); desc=font.get('/FontDescriptor')
            if desc: font_descriptors.append(desc.get_object())
    assert any('/FontFile2' in d or '/FontFile' in d or '/FontFile3' in d for d in font_descriptors)
    archive=path_at(result['team_delivery'],base)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert z.read('input/'+source.name)==source.read_bytes()
        assert z.read('booklet/instructions_cn.pdf')==files['pdf'].read_bytes()
        assert z.read('booklet/instructions_cn.html')==files['html'].read_bytes()
        for name in references: assert 'booklet/'+name in z.namelist()
    return {'pieces':report['brick_count'],'steps':report['step_count'],'pdf_pages':len(pdf.pages),
            'coverage_passed':True,'geometry_comparison':geometry_comparison,'images_passed':True,'pdf_open_passed':True,'chinese_font_embedded':True,
            'html_images_present':True,'zip_integrity_passed':True,'image_metrics':image_metrics,
            'final_stable':summary['final_stability']['stable'] if summary['final_stability'] else None,'instruction_certified':summary['instruction_certified'],
            'step_verification_status':'INCOMPLETE' if draft else 'COMPLETED',
            'stability_status':'STEP_VERIFICATION_INCOMPLETE' if draft else 'DIGITAL_CHECKS_PASSED' if summary['instruction_certified'] else 'DRAFT_REVIEW_REQUIRED',
            'static_risk_steps':summary['static_risk_steps'],'insertion_risk_steps':summary['insertion_risk_steps'],
            'topology':summary['topology'],'run':str(run),'booklet':str(book),'result':str(result_path)}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--input',type=Path,required=True)
    p.add_argument('--result',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); result=check(a.input,a.result)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'检查通过：{result["pieces"]} 件，{result["steps"]} 步，{result["pdf_pages"]} 页')
if __name__=='__main__': main()
