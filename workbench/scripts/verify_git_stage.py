"""Verify bytes actually stored in Git's index, including portable cache hashes."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import posixpath
import subprocess

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();repo=a.repo.resolve()
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args])
    names=[n.decode() for n in git('diff','--cached','--name-only','-z').split(b'\0') if n]
    assert names and all(n=='README.md' or n.startswith('workbench/') for n in names)
    assert not git('diff','--cached','--diff-filter=D','--name-only')
    assert sum(n.startswith('workbench/vendor/legolization060/legolization/') and n.endswith('.py') for n in names)==130
    forbidden={'.venv','.git','__pycache__','7ken','outputs','.setup_temp','.test_temp','detail_render_cache'}
    assert not [n for n in names if any(s in forbidden for s in PurePosixPath(n).parts)]
    assert not [n for n in names if PurePosixPath(n).suffix.lower() in ('.zip','.exe','.pyc','.whl','.mp4','.ckpt','.safetensors')]
    child=subprocess.Popen(['git','-C',str(repo),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    def blob(name):
        child.stdin.write((':'+name+'\n').encode());child.stdin.flush()
        fields=child.stdout.readline().split();assert len(fields)==3 and fields[1]==b'blob',fields
        data=child.stdout.read(int(fields[2]));assert child.stdout.read(1)==b'\n';return data
    verified=set()
    try:
        index=json.loads(blob('workbench/checkpoints/team_index.json'))
        for model in index['models']:
            for relative,expected in model['hashes'].items():
                name=posixpath.normpath('workbench/'+relative)
                assert hashlib.sha256(blob(name)).hexdigest()==expected,name
                verified.add(name)
        for line in blob('instructions/checksums.sha256').decode().splitlines():
            expected,name=line.split('  ',1)
            assert hashlib.sha256(blob('instructions/'+name)).hexdigest()==expected,name
    finally:
        child.stdin.close();child.stdout.close();child.wait()
    result={'staged_files':len(names),'vendor_python_files':130,'all_portable_index_hashes_match_staged_bytes':True,
            'verified_cache_files':len(verified),'published_instructions_unchanged':True,'no_deleted_or_forbidden_files':True}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
