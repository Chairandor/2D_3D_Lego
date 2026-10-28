"""Exact type-1 geometry comparison; does not modify, import or solve a model."""
from collections import Counter
from fractions import Fraction as F
from pathlib import Path

IDENTITY=(F(1),F(0),F(0),F(0),F(1),F(0),F(0),F(0),F(1))
ZERO=(F(0),F(0),F(0))

def key(name):return name.replace('\\','/').casefold()

def mul(a,b):
    return tuple(sum(a[3*i+k]*b[3*k+j] for k in range(3)) for i in range(3) for j in range(3))

def vec(a,x):return tuple(sum(a[3*i+k]*x[k] for k in range(3)) for i in range(3))

def records(path):
    """Leaf occurrences in source traversal order, with exact world transforms."""
    result=[]
    def collect(file,offset,rotation,colour,stack):
        file=file.resolve();sections={};current=None
        raw=file.read_text(encoding='utf-8-sig').splitlines()
        for line in raw:
            if line.upper().startswith('0 FILE '):
                current=key(line[7:].strip());sections[current]=[]
            elif current is not None:sections[current].append(line)
        if not sections:sections={key(file.name):raw}
        expand(file,sections,next(iter(sections)),offset,rotation,colour,stack)
    def expand(file,sections,name,offset,rotation,colour,stack):
        token=(str(file).casefold(),name)
        assert token not in stack,'Cyclic submodel: '+name
        for line in sections[name]:
            fields=line.strip().split(maxsplit=14)
            if not fields or fields[0]!='1':continue
            assert len(fields)==15,'Invalid type-1 record: '+line
            local=tuple(F(v) for v in fields[2:5]); matrix=tuple(F(v) for v in fields[5:14])
            point=tuple(a+b for a,b in zip(offset,vec(rotation,local))); transform=mul(rotation,matrix)
            effective=colour if fields[1]=='16' else fields[1]
            ref=key(fields[14]); record=(effective,ref,*point,*transform)
            if ref in sections:expand(file,sections,ref,point,transform,effective,stack+(token,))
            elif ref.endswith('.dat'):result.append(record)
            else:
                external=file.parent/ref
                if not external.is_file():raise FileNotFoundError('Missing geometry reference: '+str(external))
                collect(external,point,transform,effective,stack+(token,))
    collect(Path(path),ZERO,IDENTITY,'16',())
    return result

def geometry(path):
    return Counter(records(path))

def write_steps(source, destination, groups, source_refs):
    """Flatten references without rounding, snapping or changing leaf geometry."""
    from decimal import Decimal, localcontext
    def number(value):
        # LDraw decimal inputs and their sums/products have terminating decimals.
        with localcontext() as ctx:
            ctx.prec = max(80, len(str(abs(value.numerator))) + len(str(value.denominator)) + 10)
            s = format(Decimal(value.numerator) / Decimal(value.denominator), 'f')
        assert F(s) == value, 'Cannot serialize exact transform'
        return s.rstrip('0').rstrip('.') if '.' in s else s
    leaves=records(source)
    assert len(leaves)==len(source_refs), 'Strict import/source traversal coverage mismatch'
    by_id=dict(zip(source_refs,leaves,strict=True))
    lines=['0 FILE model_steps.mpd','0 Exact source geometry; solver-free assembly order']
    for group in groups:
        for brick_id in group:
            colour,part,*values=by_id[brick_id]
            lines.append('1 '+colour+' '+' '.join(number(v) for v in values)+' '+part)
        lines.append('0 STEP')
    lines.append('0 NOFILE')
    Path(destination).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return compare(source,destination)

def compare(source,export):
    before=geometry(source);after=geometry(export)
    assert before and before==after,'Part/colour/position/transform changed in exported MPD'
    return {'source_records':sum(before.values()),'export_records':sum(after.values()),
            'identical_part_colour_positions_transforms':True}
