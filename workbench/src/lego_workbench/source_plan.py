"""Reuse a model's existing STEP order via the upstream reconstruction API.

This does not run the smart planner, physics, placement or optimization.
"""
from dataclasses import asdict,replace
from pathlib import Path
import shutil
from .storage import digest,fingerprint,model_lock,read_json,write_json
from .ldraw_geometry import compare

def source_groups(refs):
    # The upstream flat reconstruction merges every submodel into its parent
    # attach STEP. Preserve child STEP boundaries from strict import refs.
    groups=[];last=None
    for brick_id,ref in refs.items():
        instances=tuple((p.source_model,p.reference,p.source_line) for p in ref.path[:-1])
        key=(ref.global_step,instances,ref.source_step)
        if key!=last:groups.append({'origin':key,'brick_ids':[]});last=key
        groups[-1]['brick_ids'].append(brick_id)
    return groups

def import_source_plan(settings,source,final_origin=None):
    from .compat.api import load_api
    from .engine import instruction_config
    from .environment import ensure_palette
    from .draft import DRAFT_FILES,checked_draft
    from .booklet.pdf import validate
    from legolization.instructions.sequencer import BuildStep,InstructionPlan
    from legolization.instructions.bom import bill_of_materials
    source=Path(source).resolve();ensure_palette(settings.ldraw_dir)
    api=load_api();config=instruction_config(api,settings)
    key=fingerprint({'mode':'source-step-import/v2','sha256':digest(source),'config':asdict(config),'ground':settings.ground})
    run=settings.output_root/('source_plan_'+digest(source)[:12]+'_'+key[:12])
    with model_lock(settings.output_root,digest(source)):
        if (run/'DRAFT_INPUT_VALIDATED.json').is_file():
            checked_draft(run,source);return run
        loaded=api['import_ldraw'](source,ground=settings.ground)
        assert loaded.load_complete and loaded.has_explicit_steps,'原模型必须完整导入并含明确 STEP；不能自行推测步骤'
        groups=source_groups(loaded.source_refs)
        plan=InstructionPlan(steps=tuple(BuildStep(index=i,brick_ids=tuple(g['brick_ids']),prefix_stable=True,prefix_max_score=0.)
                                       for i,g in enumerate(groups,1)),warnings=(),bom=bill_of_materials(loaded.layout))
        plan=replace(plan,bom=bill_of_materials(loaded.layout,plan=plan),warnings=(
            'SOURCE_STEP_ORDER_REUSED: no smart planning or per-step verification; all stability declarations remain unverified',))
        run.mkdir(parents=True,exist_ok=True)
        write_json(run/'run_identity.json',{'fingerprint':key,'source_sha256':digest(source),
                   'ground':settings.ground,'config':asdict(config),'plan_origin':'original_LDraw_STEP_annotations'})
        write_json(run/'plan.json',asdict(plan));write_json(run/'source_map.json',{str(k):asdict(v) for k,v in loaded.source_refs.items()})
        write_json(run/'source_steps.json',groups)
        (run/'bom.json').write_text(plan.bom.to_json(model_name=source.name),encoding='utf-8')
        api['write_model'](loaded.layout,run/'model_steps.mpd',plan=plan)
        geometry=compare(source,run/'model_steps.mpd');validate(read_json(run/'plan.json'),read_json(run/'bom.json'))
        if final_origin:
            origin=Path(final_origin);identity=read_json(origin/'run_identity.json');marker=read_json(origin/'FINAL_READY.json')
            assert identity['source_sha256']==digest(source) and identity['ground']==settings.ground
            assert marker['files']['final_analysis.json']==digest(origin/'final_analysis.json')
            shutil.copy2(origin/'final_analysis.json',run/'final_analysis.json')
        write_json(run/'DRAFT_INPUT_VALIDATED.json',{'schema':'lego-graphics-only-input/v1','input_sha256':digest(source),
                   'package_version':'0.6.0','ground':settings.ground,'config':asdict(config),
                   'plan_origin':'original_LDraw_STEP_annotations including child source_step via strict source_refs',
                   'pieces':len(loaded.layout),'steps':len(plan.steps),'geometry_comparison':geometry,
                   'step_verification_status':'INCOMPLETE','certification':None,
                   'files':{n:digest(run/n) for n in (*DRAFT_FILES,'final_analysis.json','run_identity.json','source_steps.json') if (run/n).is_file()}})
        checked_draft(run,source)
        print(f'原始 STEP 顺序已导入：{len(loaded.layout)} 件 / {len(plan.steps)} 步；未运行规划或物理求解。',flush=True)
        return run
