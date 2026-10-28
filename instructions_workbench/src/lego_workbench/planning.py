"""Strict import and deterministic planning. This module never calls physics."""
from dataclasses import asdict, replace
from pathlib import Path
import csv
from .storage import digest, fingerprint, read_json, write_json
from .ldraw_geometry import write_steps

def prepare(settings, source):
    from .compat.api import load_api
    from .environment import ensure_palette
    from .source_plan import source_groups
    from .booklet.pdf import validate
    from .plan_coverage import validate_nonblank_steps
    from .draft import DRAFT_FILES, checked_draft
    from legolization.instructions.sequencer import BuildStep, InstructionPlan
    from legolization.instructions.bom import bill_of_materials
    source=Path(source).resolve()
    key=fingerprint({'planner':'solver-free/v1','input':digest(source),'step_size':settings.step_size})
    run=settings.output_root/('instructions_first_'+digest(source)[:12]+'_'+key[:12])
    if (run/'DRAFT_INPUT_VALIDATED.json').is_file():
        checked_draft(run,source)
        return run
    ensure_palette(settings.ldraw_dir)
    api=load_api()
    # No grounding/translation is applied to the delivered model.
    loaded=api['import_ldraw'](source,ground=False)
    if not loaded.load_complete or not len(loaded.layout):
        raise ValueError('模型未完整严格导入；不会丢件或近似变换')
    if loaded.has_explicit_steps:
        originals=[g['brick_ids'] for g in source_groups(loaded.source_refs)]
        origin='source STEP order including child instance boundaries'
    else:
        originals=[[b.brick_id for b in sorted(loaded.layout,key=lambda b:(b.layer,b.y,b.x,b.brick_id))]]
        origin='bottom-up layer/y/x/id heuristic; no static or insertion checks'
    groups=[g[i:i+settings.step_size] for g in originals for i in range(0,len(g),settings.step_size)]
    plan=InstructionPlan(steps=tuple(BuildStep(index=i,brick_ids=tuple(g),prefix_stable=None,prefix_max_score=None)
                                    for i,g in enumerate(groups,1)),warnings=('STABILITY_PENDING: solver-free plan; unknown is not passed',),
                         bom=bill_of_materials(loaded.layout))
    plan=replace(plan,bom=bill_of_materials(loaded.layout,plan=plan))
    run.mkdir(parents=True,exist_ok=True)
    write_json(run/'run_identity.json',{'source_sha256':digest(source),'fingerprint':key,'ground':False,
                                      'plan_origin':origin,'stability_status':'PENDING'})
    write_json(run/'plan.json',asdict(plan))
    write_json(run/'source_map.json',{str(k):asdict(v) for k,v in loaded.source_refs.items()})
    (run/'bom.json').write_text(plan.bom.to_json(model_name=source.name),encoding='utf-8')
    geometry=write_steps(source,run/'model_steps.mpd',groups,loaded.source_refs)
    validate(read_json(run/'plan.json'),read_json(run/'bom.json'))
    validate_nonblank_steps(read_json(run/'plan.json'))
    assert set(loaded.source_refs)=={i for g in groups for i in g}
    with (run/'bom.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['part_key','ldraw_part','colour_code','colour_name','quantity','mass_g']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        writer.writerows(asdict(p) for p in plan.bom.total)
    write_json(run/'DRAFT_INPUT_VALIDATED.json',{'schema':'lego-graphics-only-input/v1','input_sha256':digest(source),
        'package_version':'0.6.0','ground':False,'plan_origin':origin,'pieces':len(loaded.layout),'steps':len(plan.steps),
        'geometry_comparison':geometry,'step_verification_status':'INCOMPLETE','certification':None,
        'files':{n:digest(run/n) for n in (*DRAFT_FILES,'run_identity.json','bom.csv')}})
    checked_draft(run,source)
    print(f'无求解规划完成：{len(loaded.layout)} 件 / {len(groups)} 步；覆盖与原始几何核验通过。',flush=True)
    return run
