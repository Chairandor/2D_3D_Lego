"""Final-model analysis and human-readable summaries; no geometry repair."""
from dataclasses import asdict
from html import escape
from .storage import write_json

def analyze_layout(api, layout, solver):
    result = api['analyze'](layout, solver)
    topology = api['ConnectionGraph'].from_layout(layout).topology_metrics()
    return {
        'final_stability': {'stable': result.stable, 'status': result.status,
                            'objective': result.objective, 'max_score': result.max_score,
                            'unstable_ids': sorted(result.unstable_ids)},
        'topology': {'component_count': topology.component_count,
                     'floating_count': topology.floating_count,
                     'floating_ids': sorted(topology.floating_ids)},
        'per_brick': {str(k): asdict(v) for k, v in result.scores.items()},
        'solver_config': asdict(solver),
        'meaning': 'legolization 0.6.0 的数字静力检查；目标值不是倒塌概率；没有自动修复模型。',
    }

def export_analysis(run, destination, title, plan, report):
    from .rendering.framing import restore_plan
    from .storage import digest, read_json
    restored, repaired = restore_plan(plan)
    summary = {
        'model': title, 'engine': 'legolization 0.6.0',
        'input_sha256': report['input_sha256'], 'source_run': str(run),
        'report_sha256': digest(run / 'report.json'),
        'pieces': report['brick_count'], 'steps': len(restored.steps),
        'final_stability': report['final_stability'], 'topology': report['topology'],
        'instruction_certified': bool(report['instruction_certified']),
        'static_risk_steps': [s.index for s in restored.steps if not s.prefix_stable],
        'insertion_risk_steps': [s.index for s in restored.steps if s.insertion_fragile],
        'restored_warning_flags': repaired,
        'certification': report['certification'], 'solver_config': report['instructions_config']['solver'],
        'stability_optimization': False,
        'meaning': report['interpretation'],
    }
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / 'stability_summary.json', summary)
    final_path = run / 'final_analysis.json'
    if final_path.is_file():
        write_json(destination / 'stability_per_brick.json', read_json(final_path))
    rows = ''.join('<tr><td>' + str(s.index) + '</td><td>' +
                   escape(s.submodel or ('装入 ' + s.attaches if s.attaches else '主模型')) +
                   '</td><td>' + ('通过' if s.prefix_stable else '未通过') +
                   '</td><td>' + ('有风险' if s.insertion_fragile else '未标记风险') + '</td></tr>'
                   for s in restored.steps)
    final = summary['final_stability']; topology = summary['topology']
    verdict = '通过数字检查' if summary['instruction_certified'] else '待验证草稿'
    text = f'''<!doctype html><meta charset="utf-8"><title>{escape(title)}稳定性报告</title>
<style>body{{font:16px system-ui;max-width:950px;margin:30px auto;padding:20px;line-height:1.8}}table{{border-collapse:collapse;width:100%}}th,td{{padding:7px;text-align:left;border-bottom:1px solid #ddd}}.warning{{color:#a45225}}</style>
<h1>{escape(title)} · 稳定性分析</h1><p>{summary['pieces']} 件 · {summary['steps']} 步 · {verdict}</p>
<p>最终模型静力：{'通过' if final['stable'] else '未通过'}；求解状态 {escape(final['status'])}；目标值 {final['objective']:.8g}；最大零件分数 {final['max_score']:.8g}。</p>
<p>连通组件 {topology['component_count']}；悬空件 {topology['floating_count']}；静力风险步骤 {len(summary['static_risk_steps'])}；按压插入风险步骤 {len(summary['insertion_risk_steps'])}。</p>
<p class="warning">说明书图文完成不等于实物可稳定拼搭。optimal 表示数值求解完成，不等于结构稳定；目标值不能解释为倒塌概率。</p>
<p>使用 legolization 0.6.0 的接触与地面假设：ground_pull={summary['solver_config']['ground_pull']}（为 true 时地面可提供拉力，接近底板连接假设），不能直接等同于放在光滑桌面上的模型。没有改变模型几何，也没有执行稳定性优化。</p>
<p>StableLego 既有实验另外保存在项目 history 目录，其单位、近似和求解设置不同，不能直接比较两个工具的目标值。完整参数与最早失败步骤见 JSON。</p>
<table><tr><th>步骤</th><th>构建位置</th><th>自重静力</th><th>插入风险</th></tr>{rows}</table>'''
    (destination / 'stability_report.html').write_text(text, encoding='utf-8')
    return summary
