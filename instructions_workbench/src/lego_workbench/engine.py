"""Public 0.6.0 API orchestration with separate final/plan/verify checkpoints."""
import argparse
import csv
from dataclasses import asdict
from pathlib import Path

from .analysis import analyze_layout
from .progress import monitor
from .storage import CORE_FILES, digest, fingerprint, marker_valid, read_json, save_marker, write_json

def instruction_config(api, settings):
    size = settings.step_size
    return api['InstructionsConfig'](target_step_size=size, min_step_size=min(3, size),
                                     max_step_size=10, stability_policy=settings.policy,
                                     insertion_check=True, subassemblies=True, solver=api['SolverConfig']())

def checked_run(run, source):
    from .booklet.pdf import validate
    report = read_json(run / 'report.json')
    if report['input_sha256'] != digest(source):
        raise ValueError('保存的报告与原模型不一致。')
    if (run / 'READY_FOR_RENDER').is_file():
        expected = (run / 'READY_FOR_RENDER').read_text(encoding='utf-8-sig').splitlines()
        if expected != [digest(run / n) for n in ('model_steps.mpd', 'plan.json', 'report.json')]:
            raise ValueError('模型/步骤/报告的校验标记不一致。')
    elif not marker_valid(run, 'RECOVERED_SNAPSHOT.json', CORE_FILES, report['input_sha256']):
        raise ValueError('旧结果缺少运行检查点或已核验的恢复快照。')
    if (run / 'CORE_READY.json').is_file():
        marker = read_json(run / 'CORE_READY.json')
        if not marker_valid(run, 'CORE_READY.json', CORE_FILES, marker['fingerprint']):
            raise ValueError('核心结果文件已改变。')
    plan = read_json(run / 'plan.json'); bom = read_json(run / 'bom.json')
    validate(plan, bom)
    if report['brick_count'] != bom['brick_count'] or report['step_count'] != len(plan['steps']):
        raise ValueError('报告、计划与零件数量不一致。')
    return report, plan, bom

def find_existing(settings, source, config, own):
    candidates = [own]
    seen = {own.resolve()}
    for root in (settings.output_root, *settings.reuse_roots):
        if not root.is_dir():
            continue
        found = []
        for p in root.rglob('report.json'):
            folder = p.parent
            if folder.resolve() not in seen and all((folder / n).is_file() for n in CORE_FILES):
                seen.add(folder.resolve()); found.append(folder)
        candidates.extend(sorted(found, key=lambda x: (x / 'report.json').stat().st_mtime, reverse=True))
    for folder in candidates:
        if not all((folder / n).is_file() for n in CORE_FILES):
            continue
        try:
            report = read_json(folder / 'report.json')
            if report.get('input_sha256') != digest(source) or report.get('package_version') != '0.6.0':
                continue
            if report.get('instructions_config') != asdict(config):
                continue
            # Legacy generation records 0 even when grounding was unnecessary.
            # All original continuation runs used ground=True; keep-origin runs need a new analysis.
            if report.get('ground', True) != settings.ground:
                continue
            checked_run(folder, source)
            return folder
        except (OSError, ValueError, KeyError, AssertionError) as error:
            print('跳过无法核验的旧结果：' + str(folder) + '；' + str(error), flush=True)
    return None

def generate_or_reuse(api, settings, source, model_key, title, *, prepare_only=False):
    config = instruction_config(api, settings)
    key = fingerprint({'source_sha256': digest(source), 'config': asdict(config),
                       'ground': settings.ground, 'engine': 'legolization 0.6.0', 'checkpoint_schema': 1})
    run = settings.output_root / (model_key + '_' + digest(source)[:12] + '_' + key[:12])
    found = find_existing(settings, source, config, run)
    if found:
        print(title + '：复用已有分析与步骤；不重新求解。\n' + str(found), flush=True)
        return found, True
    run.mkdir(parents=True, exist_ok=True)
    write_json(run / 'run_identity.json', {'fingerprint': key, 'source_sha256': digest(source),
                                          'config': asdict(config), 'ground': settings.ground})
    source_hash = digest(source)
    print(title + ' [1/6] 严格导入原模型：' + str(source), flush=True)
    imported = api['import_ldraw'](source, ground=settings.ground)
    layout = imported.layout
    if not imported.load_complete or not len(layout):
        raise ValueError('原模型未完整导入或模型为空。')
    source_map = {str(k): asdict(v) for k, v in imported.source_refs.items()}
    write_json(run / 'source_map.json', source_map)
    if marker_valid(run, 'FINAL_READY.json', ('final_analysis.json',), key):
        final = read_json(run / 'final_analysis.json')
        print(title + ' [2/6] 复用最终模型稳定性分析。', flush=True)
    else:
        print(title + ' [2/6] 最终模型稳定性与连通性分析；完成即保存。', flush=True)
        with monitor(run, '最终模型分析'):
            final = analyze_layout(api, layout, config.solver)
        write_json(run / 'final_analysis.json', final)
        save_marker(run, 'FINAL_READY.json', ('final_analysis.json',), key)
    from .rendering.framing import restore_plan
    if marker_valid(run, 'PLAN_READY.json', ('plan.json', 'model_steps.mpd', 'bom.json', 'steps.csv', 'bom.csv'), key):
        plan, _ = restore_plan(read_json(run / 'plan.json'))
        print(title + ' [3/6] 复用完整步骤检查点。', flush=True)
    else:
        print(title + f' [3/6] 规划拼搭步骤（目标 {settings.step_size} 件/步；长耗时会显示探测计数）。', flush=True)
        with monitor(run, '步骤规划'):
            plan = api['plan_instructions'](layout, config=config)
        # Restore 0.6.0 ROTSTEP-lost insertion flags from the plan's own warnings.
        plan, _ = restore_plan(asdict(plan))
        write_json(run / 'plan.json', asdict(plan))
        api['write_model'](layout, run / 'model_steps.mpd', plan=plan)
        (run / 'bom.json').write_text(plan.bom.to_json(model_name=source.name) + '\n', encoding='utf-8')
        with (run / 'steps.csv').open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['step', 'brick_ids', 'prefix_stable', 'insertion_fragile', 'submodel', 'attaches'])
            writer.writerows([s.index, ' '.join(map(str, s.brick_ids)), s.prefix_stable,
                              s.insertion_fragile, s.submodel, s.attaches] for s in plan.steps)
        with (run / 'bom.csv').open('w', encoding='utf-8-sig', newline='') as f:
            fields = ['part_key', 'ldraw_part', 'colour_code', 'colour_name', 'quantity', 'mass_g']
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            writer.writerows(asdict(p) for p in plan.bom.total)
        save_marker(run, 'PLAN_READY.json', ('plan.json', 'model_steps.mpd', 'bom.json', 'steps.csv', 'bom.csv'), key)
    if prepare_only:
        print(title+'：完整计划与 BOM 已保存；逐步骤验证延后，允许明确的草稿导出。',flush=True)
        return run, False
    verify_key = fingerprint({'run': key, 'plan': digest(run / 'plan.json')})
    if marker_valid(run, 'VERIFY_READY.json', ('certification.json',), verify_key):
        cert = read_json(run / 'certification.json')
        print(title + ' [4/6] 复用步骤静力与插入检查。', flush=True)
    else:
        print(title + f' [4/6] 验证覆盖、插入与各步骤静力；共 {len(plan.steps)} 步。', flush=True)
        with monitor(run, '步骤验证'):
            certification = api['certify_instructions'](layout, plan, config=config)
        cert = asdict(certification)
        write_json(run / 'certification.json', cert)
        save_marker(run, 'VERIFY_READY.json', ('certification.json',), verify_key)
    stable = all(s.prefix_stable for s in plan.steps)
    fragile = any(s.insertion_fragile for s in plan.steps)
    certified = bool(not cert['violations'] and cert['earliest_failure'] is None and stable
                     and not fragile and final['final_stability']['stable']
                     and final['topology']['component_count'] == 1 and final['topology']['floating_count'] == 0)
    report = {
        'schema': 'lego-course/workbench/v1', 'package_version': '0.6.0', 'input': str(source),
        'input_sha256': source_hash, 'brick_count': len(layout), 'step_count': len(plan.steps),
        'ground': settings.ground, 'ground_offset_plate_layers': imported.ground_offset_layers,
        'source_had_explicit_steps': imported.has_explicit_steps, 'instructions_config': asdict(config),
        'instruction_certified': certified, 'all_prefixes_stable': stable, 'insertion_fragile': fragile,
        'certification': cert, 'final_stability': final['final_stability'], 'topology': final['topology'],
        'warnings': list(plan.warnings),
        'interpretation': 'legolization 数字检查；不能替代或覆盖既有 StableLego/Gurobi 结果；目标值不是稳定概率。',
    }
    write_json(run / 'report.json', report)
    (run / 'READY_FOR_RENDER').write_text(''.join(digest(run / n) + '\n' for n in ('model_steps.mpd', 'plan.json', 'report.json')), encoding='utf-8')
    save_marker(run, 'CORE_READY.json', CORE_FILES, key)
    if digest(source) != source_hash:
        raise RuntimeError('原模型在运行期间发生变化。')
    checked_run(run, source)
    return run, False
