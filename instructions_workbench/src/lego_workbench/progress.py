"""Official telemetry provides completed probe counts, not a fake percentage."""
from contextlib import contextmanager
import threading
import time
from .storage import write_json

@contextmanager
def monitor(run, phase):
    from legolization import telemetry
    stop = threading.Event()
    guard = threading.Lock()
    state = {'phase': phase, 'status': 'running', 'completed_lp_calls': 0,
             'completed_prefix_probes': 0, 'current_call': '', 'elapsed_seconds': 0}
    started = time.monotonic()
    def sink(event, name, when, record):
        with guard:
            if event == 'start':
                state['current_call'] = name
            elif event == 'end':
                if name in ('stability.lp.linprog', 'stability.rescue.cold_direct'):
                    state['completed_lp_calls'] += 1
                if name in ('stability.prefix.probe', 'stability.prefix.press', 'stability.prefix.remove_probe'):
                    state['completed_prefix_probes'] += 1
    def persist(announce=False):
        with guard:
            state['elapsed_seconds'] = int(time.monotonic() - started)
            row = dict(state)
        write_json(run / 'progress.json', row)
        if announce:
            print(f"{phase}：已用时 {row['elapsed_seconds']} 秒；已完成 LP 调用 {row['completed_lp_calls']}，前缀探测 {row['completed_prefix_probes']}；最近调用 {row['current_call'] or '初始化'}。", flush=True)
    def beat():
        while not stop.wait(20):
            persist(True)
    worker = threading.Thread(target=beat, daemon=True)
    with telemetry.record(span_sink=sink) as record:
        persist(); worker.start()
        try:
            yield
        except BaseException:
            state['status'] = 'interrupted_or_error'
            raise
        else:
            state['status'] = 'completed'
        finally:
            stop.set(); worker.join(timeout=2)
            persist()
            write_json(run / ('telemetry_' + phase + '.json'), record.to_dict())
