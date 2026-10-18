"""Score a policy on a GMAPS parser task file with the SAME verifier NeMo Gym uses, without starting any Gym server
For the orchestrated rollouts use `gym eval run` (see gym/README.md); this is the quick path.

  python3 gym/run_offline_policy.py --input gym/environments/gmaps_parser/data/heldout5_script.jsonl --policy gold      # sanity: 1.0
  python3 gym/run_offline_policy.py --input ... --policy empty                                                         # sanity: 0.0
  python3 gym/run_offline_policy.py --input ... --policy grammar                                                       # the rule parser
  python3 gym/run_offline_policy.py --input ... --policy endpoint --base-url http://127.0.0.1:8001/v1 --model gmaps-parser
  python3 gym/run_offline_policy.py --input ... --policy endpoint --base-url http://127.0.0.1:8000/v1 --model nvidia/Qwen3.6-35B-A3B-NVFP4

`endpoint` sends responses_create_params.input as chat messages to an OpenAI-compatible server (vLLM) with thinking off,
temperature 0, and gives the verifier exactly the text the model produced. Writes per-task rows (reward, components,
predicted JSON, grounding reason, latency) and a summary. Nothing here reads truth files: gold comes from the task rows.
"""
import argparse, asyncio, json, re, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'gym'), str(REPO / 'venue'), str(REPO / 'lib')]
from resources_servers.gmaps_parser.app import GmapsParserVerifier, GmapsParserVerifyRequest, FIELDS   # noqa: E402


def fake_response(text):
    return {"id": "offline", "created_at": 0, "model": "offline", "object": "response",
            "output": [{"id": "m", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}]}],
            "parallel_tool_calls": False, "tool_choice": "none", "tools": []}


def policy_gold(task, **_):
    return json.dumps(task['gold'], separators=(',', ':'))


def policy_empty(task, **_):
    return ''


def policy_grammar(task, **_):
    """The grammar parser alone (lib/gmaps_core/parser.py), rendered into the llm_fallback schema like eval/parser_baselines.py."""
    from gmaps_core.parser import parse
    try:
        from gmaps_core.parser import classify_speaker
    except Exception:
        classify_speaker = None
    text = task['transmission']
    role = None
    if classify_speaker:
        try:
            role, _ = classify_speaker(text)
        except Exception:
            role = None
    resolver = None
    try:
        sys.path.insert(0, str(REPO / 'eval/synthetic'))
        from chart_ids import ChartIdentifiers
        resolver = ChartIdentifiers(task['airport'])
    except Exception:
        pass
    ev = parse(text, task['airport'], speaker_hint=role, resolver=resolver, event_time=0, source={'id': task.get('src_id', 'x'), 'start_s': 0, 'end_s': None})
    typed = ev.get('parse_path') == 'grammar' and ev.get('kind') not in ('OTHER', 'UNKNOWN', None) and bool(ev.get('actor_candidates'))
    if not typed:
        return ''
    twy = [x for x in ([ev.get('crossing_at')] if ev.get('crossing_at') else []) + list(ev.get('route') or []) if x]
    return json.dumps({'speaker_role': role if role in ('ATC', 'PILOT') else 'UNKNOWN', 'callsign': (ev.get('actor_candidates') or [None])[0],
                       'runway': ev.get('runway'), 'instruction': ev['kind'], 'taxiways': twy[:4]}, separators=(',', ':'))


def policy_endpoint(task, base_url, model, timeout, **_):
    import urllib.request
    if not re.match(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?/', base_url):
        raise SystemExit('endpoint must be local (127.0.0.1 / localhost): inference stays on the box')
    body = {'model': model, 'messages': task['responses_create_params']['input'], 'temperature': 0, 'max_tokens': 160,
            'chat_template_kwargs': {'enable_thinking': False}}
    req = urllib.request.Request(base_url.rstrip('/') + '/chat/completions', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return d['choices'][0]['message'].get('content') or ''


POLICIES = {'gold': policy_gold, 'empty': policy_empty, 'grammar': policy_grammar, 'endpoint': policy_endpoint}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--policy', required=True, choices=sorted(POLICIES))
    ap.add_argument('--base-url', default='http://127.0.0.1:8001/v1')
    ap.add_argument('--model', default='gmaps-parser')
    ap.add_argument('--timeout', type=float, default=30)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    tasks = [json.loads(l) for l in open(a.input)]
    if a.limit:
        tasks = tasks[:a.limit]
    verifier = GmapsParserVerifier()
    rows, t_all = [], time.time()
    for task in tasks:
        t0 = time.time()
        try:
            text = POLICIES[a.policy](task, base_url=a.base_url, model=a.model, timeout=a.timeout)
            err = None
        except Exception as e:
            text, err = '', f'{type(e).__name__}: {e}'
        lat = time.time() - t0
        req = GmapsParserVerifyRequest(**{**{k: task[k] for k in ('responses_create_params', 'gold', 'airport', 'transmission')}, 'response': fake_response(text)})
        res = await verifier.verify(req)
        rows.append({'src_id': task.get('src_id'), 'airport': task['airport'], 'text_source': task.get('text_source'), 'reward': res.reward,
                     'components': res.reward_components, 'json_valid': res.json_valid, 'groundable': res.groundable,
                     'grounding_reason': res.grounding_reason, 'predicted': res.predicted, 'gold': task['gold'], 'output_text': text,
                     'latency_s': round(lat, 3), 'error': err})
    n = len(rows)
    lat = sorted(r['latency_s'] for r in rows)
    summary = {'input': a.input, 'policy': a.policy, 'model': a.model if a.policy == 'endpoint' else a.policy, 'n': n,
               'record_exact': round(sum(r['reward'] for r in rows) / n, 3),
               'field_exact': {k: round(sum(r['components'][k] for r in rows) / n, 3) for k in FIELDS},
               'json_valid': round(sum(r['json_valid'] for r in rows) / n, 3), 'groundable': round(sum(r['groundable'] for r in rows) / n, 3),
               'record_exact_and_groundable': round(sum(1 for r in rows if r['reward'] == 1.0 and r['groundable'] == 1.0) / n, 3),
               'errors': sum(1 for r in rows if r['error']), 'latency_s': {'median': lat[n // 2], 'p95': lat[min(n - 1, int(0.95 * (n - 1)))]},
               'wall_s': round(time.time() - t_all, 1), 'SIMULATED': True,
               'note': 'same verifier as the NeMo Gym resources server; gold taxiway rule per the task file (gold_rule)'}
    out = Path(a.out) if a.out else REPO / 'gym/rollouts' / f"offline_{Path(a.input).stem}_{a.policy}{'_' + re.sub(r'[^A-Za-z0-9.-]+', '-', a.model) if a.policy == 'endpoint' else ''}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'summary': summary, 'rows': rows}, indent=1) + '\n')
    print(json.dumps(summary, indent=1)); print('wrote', out)


if __name__ == '__main__':
    asyncio.run(main())
