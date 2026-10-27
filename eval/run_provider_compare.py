#!/usr/bin/env python3
"""Nemotron via Nightshift vs the fine-tuned 4B on the same held-out set (Open Agent Hackathon 2026). SIMULATED data.

Same tasks, same prompt (the task file's responses_create_params.input: the Raleigh eval's prompt), temperature 0, max_tokens 160,
and the SAME verifier and summary as gym/run_offline_policy.py (GmapsParserVerifier: record exact, groundable = accepted by the
runtime grounding rule). No new scorer. Every call is a trace line in runs/traces/<run_id>.jsonl (provider, model_reported, latency).

  python3 eval/run_provider_compare.py --set heldout4_asr --provider nightshift --model <Nemotron id> [--deadline-utc 22:50]

If the deadline is reached, it stops after the last finished task: the result is a fixed prefix of the set, n is recorded, and
the grammar / untuned / fine-tuned rows are recomputed on exactly that prefix from their per-row files. Nothing is extrapolated.
--exclude-airport KLGA scores a subset the same way (every row of the comparison on the same tasks).
Output: eval/results/open_agent_2026/nemotron_vs_parser_<set>.json
"""
import argparse, asyncio, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'gym'), str(REPO / 'lib')]
from run_offline_policy import fake_response                                       # noqa: E402
from resources_servers.gmaps_parser.app import GmapsParserVerifier, GmapsParserVerifyRequest, FIELDS   # noqa: E402
from gmaps_core.providers import get_provider                                      # noqa: E402
from gmaps_core.providers.base import strip_reasoning                              # noqa: E402
from gmaps_core.trace import TraceLogger                                           # noqa: E402

BASELINES = {'grammar (hand-written, no model)': 'gym/rollouts/offline_{set}_grammar.json',
             'untuned base 4B (Qwen3-4B-Instruct-2507, local vLLM)': 'gym/rollouts/offline_{set}_endpoint_base.json',
             'fine-tuned 4B (gmaps-parser LoRA, local vLLM)': 'gym/rollouts/offline_{set}_endpoint_gmaps-parser.json'}


def summarize(rows):
    n = len(rows)
    lat = sorted(r['latency_s'] for r in rows if r.get('latency_s') is not None)
    return {'n': n, 'record_exact': round(sum(r['reward'] for r in rows) / n, 3) if n else None,
            'correct_and_accepted': round(sum(1 for r in rows if r['reward'] == 1.0 and r['groundable'] == 1.0) / n, 3) if n else None,
            'accepted_by_runtime': round(sum(r['groundable'] for r in rows) / n, 3) if n else None,
            'json_valid': round(sum(r['json_valid'] for r in rows) / n, 3) if n else None,
            'field_exact': {k: round(sum(r['components'][k] for r in rows) / n, 3) for k in FIELDS} if n else None,
            'errors': sum(1 for r in rows if r.get('error')),
            'latency_s_median': lat[len(lat) // 2] if lat else None}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', default='heldout4_asr')
    ap.add_argument('--provider', default='nightshift')
    ap.add_argument('--model', default=None, help='overrides NIGHTSHIFT_MODEL')
    ap.add_argument('--deadline-utc', default=None, help='HH:MM (UTC, today): stop starting new tasks after this time')
    ap.add_argument('--exclude-airport', action='append', default=[])
    ap.add_argument('--max-tokens', type=int, default=160)
    ap.add_argument('--limit', type=int, default=0, help='score only the first N tasks (a fixed prefix; n is recorded)')
    a = ap.parse_args()
    if a.model:
        os.environ['NIGHTSHIFT_MODEL'] = a.model
    prov = get_provider(a.provider)
    tasks = [json.loads(l) for l in open(REPO / f'gym/environments/gmaps_parser/data/{a.set}.jsonl')]
    tasks = [t for t in tasks if t['airport'] not in a.exclude_airport]
    if a.limit:
        tasks = tasks[:a.limit]
    deadline = None
    if a.deadline_utc:
        h, m = map(int, a.deadline_utc.split(':'))
        deadline = datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)
    run_id = f"compare-{a.set}-{prov.name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
    tr = TraceLogger(run_id, scenario=f'heldout:{a.set}')
    verifier = GmapsParserVerifier()
    rows, stopped = [], None
    for i, task in enumerate(tasks):
        if deadline and datetime.now(timezone.utc) >= deadline:
            stopped = f'deadline {a.deadline_utc} UTC reached after {i} of {len(tasks)} tasks (fixed prefix, in file order)'
            break
        msgs = task['responses_create_params']['input']
        r = prov.chat(msgs, temperature=0, max_tokens=a.max_tokens)
        text = strip_reasoning(r['content']) if r['ok'] else ''
        req = GmapsParserVerifyRequest(**{**{k: task[k] for k in ('responses_create_params', 'gold', 'airport', 'transmission')}, 'response': fake_response(text)})
        res = await verifier.verify(req)
        outcome = 'unavailable' if not r['ok'] else ('accepted' if res.groundable == 1.0 else 'rejected')
        tr.log_call('heldout_parse', 'executor', r, outcome, grounding=res.grounding_reason, src_id=task.get('src_id'), reward=res.reward)
        rows.append({'src_id': task.get('src_id'), 'airport': task['airport'], 'reward': res.reward, 'components': res.reward_components,
                     'json_valid': res.json_valid, 'groundable': res.groundable, 'grounding_reason': res.grounding_reason,
                     'predicted': res.predicted, 'output_text': r['content'], 'model_reported': r.get('model_reported'),
                     'latency_s': round(r['latency_ms'] / 1000, 3) if r.get('latency_ms') is not None else None, 'error': r.get('error')})
        print(f"{i + 1}/{len(tasks)} reward={res.reward} accepted={res.groundable} {r.get('latency_ms')} ms {r.get('error') or ''}", flush=True)
    ids = {r['src_id'] for r in rows}
    comparison = []
    for name, path in BASELINES.items():
        p = REPO / path.format(set=a.set)
        if not p.exists():
            comparison.append({'system': name, 'source': str(p.relative_to(REPO)), 'missing': True}); continue
        base_rows = [r for r in json.loads(p.read_text())['rows'] if r['src_id'] in ids]
        comparison.append({'system': name, 'source': str(p.relative_to(REPO)), **summarize(base_rows),
                           'latency_note': 'local vLLM on the DGX Spark (Raleigh run)' if 'endpoint' in path else 'grammar, no model'})
    reported = sorted({r['model_reported'] for r in rows if r['model_reported']})
    comparison.append({'system': f'Nemotron via {prov.name}', 'source': f'runs/traces/{run_id}.jsonl', 'model_requested': getattr(prov, 'model', None),
                       'model_reported': reported, **summarize(rows), 'latency_note': f'{prov.name} round trip (network included)'})
    out = REPO / 'eval/results/open_agent_2026' / (f'nemotron_vs_parser_{a.set}' + ('_excl_' + '_'.join(a.exclude_airport) if a.exclude_airport else '') + '.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {'SIMULATED': True, 'set': a.set, 'tasks_in_set': len(tasks), 'n_scored': len(rows), 'stopped': stopped,
           'excluded_airports': a.exclude_airport, 'same_prompt': 'task responses_create_params.input (as gym/run_offline_policy.py endpoint policy)',
           'scorer': 'gym/resources_servers/gmaps_parser/app.py GmapsParserVerifier (unchanged)',
           'note': 'baseline rows recomputed from their per-row files on exactly the same src_ids as the Nemotron rows',
           'comparison': comparison, 'rows': rows}
    out.write_text(json.dumps(doc, indent=1) + '\n')
    print(json.dumps(comparison, indent=1)); print('wrote', out.relative_to(REPO), '· trace', tr.path.relative_to(REPO))


if __name__ == '__main__':
    asyncio.run(main())
