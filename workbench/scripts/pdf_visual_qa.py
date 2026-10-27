"""Render final PDFs for inspection, without invoking any model pipeline."""
import argparse
import json
from pathlib import Path
import subprocess
from PIL import Image, ImageDraw
import pdfplumber

def main():
    p=argparse.ArgumentParser(); p.add_argument('--pdf',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True); p.add_argument('--pdftoppm',required=True)
    a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    state=a.out/'rendered.json'
    if not state.is_file():
        subprocess.run([a.pdftoppm,'-r','65','-png',str(a.pdf),str(a.out/'page')],check=True)
        pages=sorted(a.out.glob('page-*.png'))
        bounds=[]
        with pdfplumber.open(a.pdf) as doc:
            assert len(doc.pages)==len(pages)
            for n,page in enumerate(doc.pages,1):
                for c in page.chars:
                    if c['x0'] < -1 or c['x1']>page.width+1 or c['top'] < -1 or c['bottom']>page.height+1:
                        bounds.append({'page':n,'text':c['text']})
        for offset in range(0,len(pages),36):
            group=pages[offset:offset+36]; sheet=Image.new('RGB',(1440,6*354),'#cbd1d5'); draw=ImageDraw.Draw(sheet)
            for i,path in enumerate(group):
                im=Image.open(path).convert('RGB'); im.thumbnail((230,326))
                x=(i%6)*240+5; y=(i//6)*354+22
                sheet.paste(im,(x,y)); draw.text((x,y-19),path.name,fill='black')
            sheet.save(a.out/f'contact_{offset//36+1:02d}.jpg',quality=90)
        state.write_text(json.dumps({'pdf':str(a.pdf),'pages':len(pages),'text_bounds_issues':bounds,
                                    'contact_sheets':len(list(a.out.glob('contact_*.jpg')))},ensure_ascii=False,indent=2),encoding='utf-8')
    print(state.read_text(encoding='utf-8'))
if __name__=='__main__': main()
