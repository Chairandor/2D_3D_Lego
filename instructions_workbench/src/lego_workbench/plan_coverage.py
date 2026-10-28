"""A subassembly attachment is a real instruction, with zero new bricks."""
def validate_nonblank_steps(plan):
    subs={s['name']:s for s in plan.get('subassemblies',[])}
    built=set()
    for step in plan['steps']:
        if step['brick_ids']:
            if step.get('submodel'):built.add(step['submodel'])
            continue
        name=step.get('attaches')
        assert name and name in subs and name in built,'存在没有新零件或已构建子组件的空白步骤'
        assert subs[name]['brick_ids'],'装入的子组件为空'
