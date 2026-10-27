"""Create a portable reading/cache index from the already audited delivery.

No model import, planning, rendering or analysis. Old markers are NOT rewritten
or fabricated. Index hashes attest copied artifacts, not physical certification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

def main():
    p=argparse.ArgumentParser();p.add_argument('--delivery',type=Path,required=True)
    p.add_argument('--published',type=Path,required=True);p.add_argument('--project',type=Path,required=True)
    args=p.parse_args();project=args.project.resolve();delivery=args.delivery.resolve()
    manifest=load(args.published/'manifest.json');batch=load(delivery/'batch_results.json')
    by_id={r['id']:r for r in batch['models']};rows=[];inputs=[]
    for row in manifest['models']:
        if row['id'] not in by_id:continue # Legacy PDFs have no model inputs here.
        name=row['id'];old=delivery/'instructions'/name
        source=delivery/by_id[name]['input'];assert sha(source)==row['input_sha256']
        assert row['input_sha256']!='dbf917b222136797636177c8ce1a104581e050f962f94c6b04ccc1b33790d056'
        target=project/'inputs/team'/name/source.name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
        run=project/'checkpoints/team'/name;run.mkdir(parents=True,exist_ok=True)
        for leaf in ('plan.json','bom.json','source_map.json','model_steps.mpd','final_analysis.json','certification.json','bom.csv'):
            if (old/'analysis_and_steps'/leaf).is_file():shutil.copy2(old/'analysis_and_steps'/leaf,run/leaf)
        def rel(path):return Path(__import__('os').path.relpath(path,project)).as_posix()
        booklet=args.published/name
        files={key:rel(args.published/row[key])
               for key in ('pdf','html','bom','stability_report')}
        files.update(model_steps=rel(run/'model_steps.mpd'),stability_summary=rel(booklet/'stability_summary.json'))
        hashed=[p for p in booklet.rglob('*') if p.is_file() and p.suffix.lower() in ('.pdf','.html','.png','.json','.csv')]
        hashed.extend(p for p in run.iterdir() if p.is_file());hashed.append(target)
        incomplete=row['step_verification_status']=='INCOMPLETE'
        state='TIMEOUT' if incomplete else 'PASSED' if row['instruction_certified'] else 'FAILED'
        rows.append({'id':name,'model':row['name'],'input_sha256':row['input_sha256'],'booklet_complete':True,
            'generation_status':'PASSED','stability_status':state,'instruction_certified':row['instruction_certified'],
            'checks':{k:row[k] for k in ('pieces','steps','pdf_pages')},'files':files,
            'hashes':{rel(p):sha(p) for p in hashed},'analysis_reused':True,
            'reuse_note':'历史成品和原始逐件计划复用；未完成静力预算已经耗尽，不自动重试。' if incomplete else '历史分析完成；已知风险保留。',
            'original_plan_sha256':sha(run/'plan.json')})
        inputs.append({'id':name,'name':row['name'],'studs':by_id[name]['studs'],'input':rel(target),
                       'sha256':row['input_sha256'],'expected_pieces':row['pieces'],'reuse_only':True})
    save(project/'checkpoints/team_index.json',{'schema':'audited-team-cache/v1','models':rows,
         'meaning':'成品文件完整性与来源核验；不是重新生成的认证标记。','exclusions':manifest['excluded']})
    save(project/'config/batch_manifest.json',{'models':inputs,'excluded':manifest['excluded']})
    print(json.dumps({'models':len(rows),'pdfs':manifest['pdf_count'],'sha256_index':sha(project/'checkpoints/team_index.json')}))
if __name__=='__main__':main()
