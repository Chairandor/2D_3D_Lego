"""Copy only runnable source/resources into a separate publication checkout."""
import argparse
from pathlib import Path
import shutil

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--target',type=Path,required=True)
    args=p.parse_args();source=args.source.resolve();target=args.target.resolve()
    target.mkdir(parents=True,exist_ok=True)
    def copy(path):
        dest=target/path.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    for folder in ('src','vendor','assets','tests','.vscode'):
        for path in (source/folder).rglob('*'):
            if not path.is_file():continue
            rel=path.relative_to(source)
            if any(s in ('__pycache__','build','.venv') or s.endswith('.egg-info') for s in rel.parts):continue
            if path.suffix.lower() in ('.pyc','.whl','.zip','.exe'):continue
            copy(path)
    for leaf in ('Run.ps1','Batch.ps1','Setup.ps1','run.py','batch.py','requirements.txt','requirements-lock.txt','README.md','Lego_Workbench.code-workspace'):
        copy(source/leaf)
    for leaf in ('执行流程与状态.md','验证记录_20261027.md','verification_20261027.json','source_provenance.json'):copy(source/'docs'/leaf)
    copy(source/'config/portable.example.json')
    for leaf in ('stage_runnable.py','prepare_team_cache.py','package_release.py','verify_release.py','verify_git_stage.py','pdf_visual_qa.py'):copy(source/'scripts'/leaf)
    (target/'.gitignore').write_text('.venv/\n__pycache__/\n*.pyc\noutputs/\n.setup_temp/\n.test_temp/\nconfig/local.json\n*.egg-info/\nvendor/**/build/\n!inputs/team/*/model.mpd\n!checkpoints/team/*/model_steps.mpd\n!vendor/legolization060/legolization/\n!vendor/legolization060/legolization/**\n',encoding='utf-8',newline='\n')
    (target/'.gitattributes').write_text('checkpoints/** -text\ninputs/** -text\n*.ttf binary\n*.png binary\n*.pdf binary\n',encoding='utf-8',newline='\n')
    print('Runnable source staged at '+str(target))
if __name__=='__main__':main()
