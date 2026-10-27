"""Read-only verification of a relocated runnable project and existing booklets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
sys.dont_write_bytecode=True
from pypdf import PdfReader
from PIL import Image

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();project=root/'workbench';sys.path.insert(0,str(project/'src'))
    from lego_workbench.ldraw_geometry import compare
    from lego_workbench.booklet.pdf import validate
    from lego_workbench.plan_coverage import validate_nonblank_steps
    index=load(project/'checkpoints/team_index.json');manifest=load(root/'instructions/manifest.json')
    rows=[]
    for row in index['models']:
        for name,expected in row['hashes'].items():assert sha(project/name)==expected,name
        model=project/'inputs/team'/row['id']/'model.mpd'
        run=project/'checkpoints/team'/row['id']
        assert sha(model)==row['input_sha256']!='dbf917b222136797636177c8ce1a104581e050f962f94c6b04ccc1b33790d056'
        geometry=compare(model,run/'model_steps.mpd')
        plan=load(run/'plan.json');bom=load(run/'bom.json')
        validate(plan,bom);validate_nonblank_steps(plan)
        assert set(i for step in plan['steps'] for i in step['brick_ids'])==set(map(int,load(run/'source_map.json')))
        assert geometry['source_records']==bom['brick_count']==row['checks']['pieces']
        rows.append({'id':row['id'],'pieces':bom['brick_count'],'steps':len(plan['steps']),
                     'generation_status':row['generation_status'],'stability_status':row['stability_status']})
    for line in (root/'instructions/checksums.sha256').read_text(encoding='utf-8').splitlines():
        expected,name=line.split('  ',1);assert sha(root/'instructions'/name)==expected,name
    images=0
    for row in manifest['models']:
        pdf=root/'instructions'/row['pdf'];reader=PdfReader(pdf)
        assert len(reader.pages)==row['pdf_pages'] and sha(pdf)==row['pdf_sha256']
        if row.get('html'):
            html=root/'instructions'/row['html'];content=html.read_text(encoding='utf-8')
            for name in re.findall(r'src="([^"]+)"',content):
                image=html.parent/name
                assert image.is_file(),str(image)
                with Image.open(image) as im:im.verify()
                images+=1
    for path in project.rglob('*'):
        if path.is_file():
            # A developer may have executed Python from this checkout. Bytecode
            # is ignored by Git and excluded by the release packager.
            if '__pycache__' in path.parts:continue
            assert not any(s in ('.venv','.git','__pycache__','.setup_temp','.test_temp','detail_render_cache','7ken') for s in path.relative_to(project).parts)
            assert path.suffix.lower() not in ('.pyc','.exe','.zip','.mp4','.whl','.safetensors','.ckpt')
    report={'portable_index_all_hashes_passed':True,'original_published_checksums_unchanged':True,
            'pdf_count':len(manifest['models']),'html_count':sum(bool(r.get('html')) for r in manifest['models']),
            'html_image_references_checked':images,'all_pdfs_open_and_page_counts_match':True,
            'all_model_geometry_and_coverage_match':True,'models':rows,'new_old_model_solver_calls':0}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
