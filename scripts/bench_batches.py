"""Paired RPC benchmark: sequential client calls vs one parallel() batch.

Real TCP RPC + generated hermes_tools client + real model_tools dispatch on
real repo files, alternating lane order per iteration to cancel drift.
Warm medians/p95; parity asserted every iteration.
"""
import json
import socket
import statistics
import sys
import threading
import time
import uuid

HERMES = sys.argv[1] if len(sys.argv) > 1 else '/home/rafail/.hermes/hermes-agent'
sys.path.insert(0, HERMES)
PLUGIN = sys.argv[2] if len(sys.argv) > 2 else '/home/rafail/projects/trix-toolrush'
sys.path.insert(0, PLUGIN)

import hermes_cli.config as config
config.load_config_readonly = lambda: {'trix-toolrush': {'enabled': True, 'parallel_reads': True}}

import importlib.util
spec = importlib.util.spec_from_file_location('c', f'{PLUGIN}/compat.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
status = c.install()
assert all(v['status'] == 'ready' for v in status.values()), status

import os
from tools import code_execution_tool as cet
from tools import file_tools as ft

# The harness read-dedup cache would answer repeat reads with an
# 'unchanged' stub and mask the transport we are measuring. Neutralize it
# for this benchmark process only (reset between iterations instead).
_RESET_DEDUP = ft.reset_file_dedup

server = socket.socket()
server.bind(('127.0.0.1', 0))
server.listen()
endpoint = 'tcp://127.0.0.1:' + str(server.getsockname()[1])
os.environ['HERMES_RPC_SOCKET'] = endpoint
os.environ['HERMES_RPC_TOKEN'] = 'bench-token'
os.environ.pop('HERMES_RPC_PERSISTENT', None)

stop = threading.Event()
log = []
counter = [0]
thread = threading.Thread(
    target=cet._rpc_server_loop,
    args=(server, "trix-bench", log, counter, 100000,
          frozenset(cet.SANDBOX_ALLOWED_TOOLS), stop, 'bench-token'),
    daemon=True)
thread.start()
module = {}
exec(cet.generate_hermes_tools_module(list(cet.SANDBOX_ALLOWED_TOOLS)), module)

WORKLOADS = {
    # 4 distinct files: repeat reads inside one lane hit the harness
    # read-dedup stub sequentially but are treated as independent batch
    # members by parallel() — different semantics, not comparable.
    'four_reads': [{'tool': 'read_file', 'args': {'path': f'{HERMES}/tools/{f}', 'limit': 40}}
                   for f in ('file_tools.py', 'file_operations.py', 'code_execution_tool.py', 'registry.py')],
    'four_searches': [{'tool': 'search_files', 'args': {'pattern': p, 'path': f'{HERMES}/tools', 'limit': 10}}
                      for p in ('def search', 'def read', 'registry', 'snapshot')],
    'mixed': [{'tool': 'read_file', 'args': {'path': f'{HERMES}/pyproject.toml', 'limit': 20}},
              {'tool': 'search_files', 'args': {'pattern': 'version', 'path': f'{HERMES}/tools', 'limit': 5}},
              {'tool': 'read_file', 'args': {'path': f'{HERMES}/README.md', 'limit': 20}},
              {'tool': 'search_files', 'args': {'pattern': 'import', 'path': f'{HERMES}/tools', 'limit': 5}}],
    'tiny_reads': [{'tool': 'read_file', 'args': {'path': f'{HERMES}/{f}', 'limit': 5}}
                   for f in ('.python-version', '.nvmrc', '.dockerignore', '.gitignore')],
}


def fresh(calls):
    out = json.loads(json.dumps(calls))
    for call in out:
        call['args'] = dict(call['args'], task_id='bench-' + uuid.uuid4().hex)
    return out


def run_bench(name, calls, iterations=13):
    samples = {'sequential': [], 'parallel': []}
    reference = None
    for i in range(iterations):
        order = ('sequential', 'parallel') if i % 2 == 0 else ('parallel', 'sequential')
        for lane in order:
            _RESET_DEDUP()
            ft.notify_other_tool_call('trix-bench')  # reset consecutive-search counter
            batch = fresh(calls)
            t = time.monotonic()
            if lane == 'sequential':
                result = []
                for cl in batch:
                    fn = module['read_file' if cl['tool'] == 'read_file' else 'search_files']
                    kwargs = {k: v for k, v in cl['args'].items() if k != 'task_id'}
                    result.append(fn(**kwargs))
            else:
                result = module['parallel'](batch)
            samples[lane].append((time.monotonic() - t) * 1000)
            parsed = [json.loads(r) if isinstance(r, str) else r for r in result] \
                if lane == 'sequential' else (json.loads(result) if isinstance(result, str) else result)
            values = [json.dumps({k: v for k, v in x.items() if k != '_warning'},
                                 sort_keys=True)[:200] for x in parsed]
            if reference is None:
                reference = values
            if values != reference:
                import sys as _sys
                for j, (x, y) in enumerate(zip(reference, values)):
                    if x != y:
                        print(f'PARITY-DIFF {name}/{lane} item {j}:', file=_sys.stderr)
                        print(f'  ref: {x[:160]}', file=_sys.stderr)
                        print(f'  got: {y[:160]}', file=_sys.stderr)
                        break
                raise AssertionError(f'parity broken in {name}/{lane}')

    def stats(xs):
        warm = xs[1:]  # cold sample separated
        ordered = sorted(warm)
        return {'median_ms': round(statistics.median(ordered), 1),
                'p95_ms': round(ordered[int(0.95 * len(ordered)) - 1], 1),
                'cold_ms': round(xs[0], 1)}

    return {'sequential': stats(samples['sequential']),
            'parallel': stats(samples['parallel']),
            'speedup': round(statistics.median(samples['sequential'][1:])
                             / statistics.median(samples['parallel'][1:]), 2)}


results = {'method': 'paired alternating, real TCP RPC, real dispatch, parity asserted per iteration',
           'python': list(sys.version_info[:2]), 'workloads': {}}
for name, calls in WORKLOADS.items():
    results['workloads'][name] = run_bench(name, calls)
    print(name, results['workloads'][name], flush=True)

stop.set()
server.close()
print(json.dumps(results, indent=1))
