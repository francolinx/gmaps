#!/usr/bin/env python3
"""Run the GMAPS open agent loop on a SIM-1 scenario (Open Agent Hackathon 2026). SIMULATED.

usage:
  python3 demo/run_open_agent.py --scenario SIM_E1_CROSS_DURING_ROLLOUT --provider sample        # no credentials, no network
  python3 demo/run_open_agent.py --scenario all --provider nightshift --record                    # live; saves the traces as samples
Outputs: runs/traces/<run_id>.jsonl (trace), samples/open_agent/<scenario>.<provider>.json (inputs/outputs),
ui_scaffold/samples/open_agent_status.json (model + step for the demo page's status line).
--record copies a live run's trace to samples/open_agent/traces/<scenario>.jsonl, which the sample provider replays.
"""
import argparse, json, os, shutil, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT))
from agent.open_agent_loop import OpenAgent, SCENARIOS          # noqa: E402
from gmaps_core.trace import TraceLogger                         # noqa: E402
from gmaps_core.providers import get_provider                    # noqa: E402
from gmaps_core.data import get_store                            # noqa: E402
from gmaps_core.engines import get_runway_memory                 # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', default='all', help='one of ' + ', '.join(SCENARIOS) + ', or all')
    ap.add_argument('--provider', default=None, help='nightshift | sample | local (default: GMAPS_PROVIDER, else nightshift if keyed, else sample)')
    ap.add_argument('--record', action='store_true', help='save this (live) run as the sample-mode fixture')
    ap.add_argument('--t-end', type=int, default=100)
    a = ap.parse_args()
    scen = SCENARIOS if a.scenario == 'all' else (a.scenario,)
    out_all = []
    for s in scen:
        prov = get_provider(a.provider)
        store, mem = get_store(), get_runway_memory()
        run_id = f"{s}-{prov.name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
        tr = TraceLogger(run_id, scenario=s)
        out = OpenAgent(s, prov, store, mem, tr, t_end=a.t_end).run()
        dst = ROOT / 'samples' / 'open_agent' / f'{s}.{prov.name}.json'
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(out, indent=1, default=str) + '\n')
        if a.record:
            if prov.name != 'nightshift':
                sys.exit('--record only records live nightshift runs')
            shutil.copy(tr.path, ROOT / 'samples' / 'open_agent' / 'traces' / f'{s}.jsonl')
        # model calls for the LaGuardia view's Nemotron indicator (a sample replay never overwrites a recorded live run)
        calls_file = ROOT / 'ui_scaffold' / 'samples' / f'nemotron_calls_{s}.json'
        if prov.name == 'nightshift' or not calls_file.exists():
            rows = [json.loads(l) for l in tr.path.read_text().splitlines()]
            calls = [{'t': r.get('t'), 'role': r['role'], 'step': r['step'], 'provider': r['provider'], 'model_reported': r.get('model_reported'),
                      'latency_ms': r.get('latency_ms'), 'outcome': r['outcome'], 'round': r.get('round'), 'replayed': bool(r.get('replayed_from'))}
                     for r in rows if r.get('provider') and r['step'] not in ('start', 'end')]
            calls_file.write_text(json.dumps({'scenario': s, 'provider': prov.name, 'run_id': run_id, 'trace': out['trace'],
                                              'recorded_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'calls': calls,
                                              'note': 'SIMULATED scenario; scenario-time t of each model call; latency is the real round trip'}, indent=1) + '\n')
        sm = out['summary']
        print(f"{s}  provider={sm['provider']}  model_reported={sm['model_reported'] or '-'}  model_calls={sm['model_calls']}  "
              f"latency_median_ms={sm['latency_ms_median']}  alerts={sm['alerts']}  trace={out['trace']}")
        for al in out['alerts']:
            print(f"  t=+{al['t']:>3}  {al['severity']:<10} {al['headline']}\n         {al['explanation']}\n         [explanation: {al['explanation_source']}]")
        out_all.append(out)
    last = out_all[-1]
    steps = [json.loads(l) for l in (ROOT / last['trace']).read_text().splitlines()]
    status = {'scenario': last['summary']['scenario'], 'provider': last['summary']['provider'], 'model_reported': last['summary']['model_reported'],
              'steps': [{'t': x.get('t'), 'role': x['role'], 'step': x['step'], 'outcome': x['outcome'], 'model_reported': x.get('model_reported')}
                        for x in steps if x['role'] in ('executor', 'explainer', 'evaluator', 'risk', 'alert')][-12:], 'note': 'SIMULATED'}
    (ROOT / 'ui_scaffold' / 'samples' / 'open_agent_status.json').write_text(json.dumps(status, indent=1) + '\n')


if __name__ == '__main__':
    main()
