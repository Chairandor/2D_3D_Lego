"""Build a new complete handoff from a published repo, with no environments/cache."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

FORBIDDEN={'.git','.venv','__pycache__','node_modules','.setup_temp','.test_temp','detail_render_cache','weights','outputs'}
def allowed(p):
    return not any(s in FORBIDDEN or s.endswith('.egg-info') for s in p.parts) and p.suffix.lower() not in ('.pyc','.whl','.exe','.zip','.mp4','.safetensors','.ckpt')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True);args=parser.parse_args()
    repo=args.repo.resolve();root=args.output_root/('Lego_Team_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    root.mkdir(parents=True,exist_ok=False)
    for group in ('workbench','instructions'):
        for source in (repo/group).rglob('*'):
            relative=source.relative_to(repo)
            if source.is_file() and allowed(relative):
                if relative.as_posix()=='workbench/config/local.json':continue
                dest=root/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
    (root/'README.md').write_text('# 乐高课程完整交付\n\n在 VS Code 打开 `workbench/Lego_Workbench.code-workspace`，安装、配置和运行命令见 [工程中文 README](workbench/README.md)。现有说明书见 [索引](instructions/README.md)。\n\n九个本次新成品及指定小鸡、兔子共 11 份 PDF 原样收录；五个模型逐步静力仍未完成，其风险不因说明书生成而变成通过。新入口先输出说明书，再自动做累计 30 分钟的分析。\n',encoding='utf-8',newline='\n')
    revision=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    (root/'RELEASE.json').write_text(json.dumps({'commit':revision,'branch':'instructions',
        'repository':'https://github.com/Chairandor/2D_3D_Lego','flow':'instructions-first/v1'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    files=[p for p in root.rglob('*') if p.is_file()]
    checks=''.join(sha(p)+'  '+p.relative_to(root).as_posix()+'\n' for p in sorted(files))
    (root/'checksums.sha256').write_text(checks,encoding='utf-8',newline='\n')
    archive=root.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob('*'):
            if p.is_file():z.write(p,root.name+'/'+p.relative_to(root).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for line in checks.splitlines():
            expected,name=line.split('  ',1)
            assert hashlib.sha256(z.read(root.name+'/'+name)).hexdigest()==expected
    result={'directory':str(root),'zip':str(archive),'zip_sha256':sha(archive),'files':len(files)+1,
            'pdfs':len(list((root/'instructions').rglob('*.pdf'))),'html_booklets':len(list((root/'instructions').rglob('instructions_cn.html'))),
            'all_zip_members_and_checksums_verified':True}
    (args.output_root/'LATEST_DELIVERY.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
