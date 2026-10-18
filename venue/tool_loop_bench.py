"""Tool-calling reliability bench for venue/tool_loop.py (cloud prep, Oct 16 night). Measures the MODEL, not the loop.

The loop already enforces the rules in code; this bench asks how often a given model follows the five-tool procedure
without needing them, under repeatable conditions, and reports it as numbers a judge can read:

  scenario   what is different                       what a reliable model does
  positive   E1 with the observation feed            parse -> ingest -> lookahead -> open_warnings -> post the one new CONFLICT once
  no_feed    E1 without any feed                     same, but lookahead answers prediction_unavailable: say so, never ask twice
  transient  E1, the first ledger_ingest call fails   retry that call once (recover), then carry on
  stale      E1, the first lookahead is forced to     report "unavailable" for that turn, no retry, still post the warning
             prediction_unavailable (injected)

Reference ("oracle") = the same deterministic tools driven by the written procedure, computed fresh for every run; the model
run is scored against it on: every typed event ingested exactly once (ingest recall), exactly the reference warnings posted,
once each (alert exact), a lookahead at as_of = t in every turn, a summary every turn, "unavailable" said whenever a lookahead
answered so, no refused retries / re-posts, and recovery after every injected fault. A run passes only if all hold.

  python3 venue/tool_loop_bench.py --base-url http://127.0.0.1:8001/v1 --model gmaps-parser --repeats 3
  python3 venue/tool_loop_bench.py --mock good --repeats 1 --out /tmp/x          # CPU self-check, no model
Outputs: <out>/toolbench_<model>_<run>.json (per run + per scenario summary) and a Markdown table on stdout; tools/scoreboard.py
reads the JSON. Fictional SIM-1 / XSIM only; post_alert stays a dry run; the model endpoint must be local.
"""
import argparse, json, statistics, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(HERE), str(REPO / 'lib')]
import gmaps_mcp as M        # noqa: E402
import replay_emit           # noqa: E402
import tool_loop as TL       # noqa: E402

SCENARIOS = {
    'positive': {'feed': True, 'faults': []},
    'no_feed': {'feed': False, 'faults': []},
    'transient': {'feed': True, 'faults': [('transient', 'ledger_ingest', 1)]},
    'stale': {'feed': True, 'faults': [('unavailable', 'lookahead', 1)]},
}


def wsig(w):
    """Warning signature that is stable across processes (warning ids are a process counter)."""
    a, b = w.get('authorization_a') or {}, w.get('authorization_b') or {}
    return f"{w['rule_id']}|{w.get('runway')}|{w.get('t_fired')}|{'+'.join(sorted(str(x.get('callsign')) for x in (a, b)))}"


class FaultyTools:
    """Wraps GmapsTools.call: the first n calls to <tool> fail in the chosen way; everything else passes through unchanged."""
    def __init__(self, tools, faults):
        self.tools = tools
        self.faults = [{'kind': k, 'tool': t, 'left': n} for k, t, n in faults]
        self.injected = []

    def call(self, name, args):
        for f in self.faults:
            if f['tool'] == name and f['left'] > 0:
                f['left'] -= 1
                self.injected.append({'kind': f['kind'], 'tool': name, 'args': args, 'wall': time.time()})
                if f['kind'] == 'transient':
                    raise M.ToolError('transient backend error (injected by the bench); retry this exact call once')
                if f['kind'] == 'unavailable':
                    return {'as_of': float(args.get('as_of', 0)), 'status': 'prediction_unavailable',
                            'reason': 'observation feed stale (injected by the bench)', 'note': 'Prediction unavailable is not "no conflict".'}
        return self.tools.call(name, args)

    def __getattr__(self, k):
        return getattr(self.tools, k)


def reference(episode, feed):
    """Drive the tools with the written procedure. Returns per-turn expectations and the set of warning signatures to post."""
    T = M.GmapsTools(episode, 'oracle', send=False, input_mode='text')
    posted, turns = set(), []
    for tx in TL.episode_transmissions(episode):
        if feed:
            replay_emit.emit(episode, tx['t'])
        ev = T.call('parse_transmission', {'text': tx['text'], 'airport': 'XSIM', 'source_id': tx['source_id'], 't': tx['t']})
        T.call('ledger_ingest', {'event_id': ev['event_id']})
        la = T.call('lookahead', {'as_of': tx['t']})
        ow = T.call('open_warnings', {})
        to_post = [wsig(w) for w in ow['open_warnings'] if w['severity'] == 'CONFLICT' and wsig(w) not in posted]
        posted.update(to_post)
        turns.append({'source_id': tx['source_id'], 't': tx['t'], 'kind': ev['kind'], 'ingest': True,
                      'unavailable': la.get('status') == 'prediction_unavailable', 'to_post': to_post})
    return {'turns': turns, 'posted': sorted(posted), 'ingest_sources': [t['source_id'] for t in turns]}


def run_once(episode, scenario, base_url, model, out_dir, run_id, max_rounds, procedure='v6'):
    cfg = SCENARIOS[scenario]
    root = Path(tempfile.mkdtemp(prefix='toolbench_'))
    M.RUNTIME, replay_emit.FEED = root, root / 'feed'
    ref = reference(episode, cfg['feed'])
    base = M.GmapsTools(episode, run_id, send=False, input_mode='text')
    tools = FaultyTools(base, cfg['faults'])
    trace = out_dir / f'trace_{run_id}.jsonl'
    loop = TL.Loop(tools, base_url, model, trace, max_rounds=max_rounds, system=TL.PROCEDURES[procedure])
    turns, t_alert, t_first = [], None, None
    for tx, exp in zip(TL.episode_transmissions(episode), ref['turns']):
        if cfg['feed']:
            replay_emit.emit(episode, tx['t'])
        t0 = time.perf_counter()
        final, rec = loop.turn(tx)
        wall = round(time.perf_counter() - t0, 3)
        # exact facts from the trace: this turn's records start at its turn_start
        allrec = [json.loads(l) for l in open(trace)]
        start = max(i for i, t in enumerate(allrec) if t.get('kind') == 'turn_start' and t.get('source_id') == tx['source_id'])
        tr = allrec[start:]
        la_results = [t for t in tr if t.get('kind') == 'tool' and t['name'] == 'lookahead' and not t['error']]
        posts = [t for t in tr if t.get('kind') == 'tool' and t['name'] == 'post_alert' and not t['error']
                 and (t['result'] or {}).get('status') in ('dry_run_not_sent', 'sent')]
        # Look-ahead evidence for this turn: an explicit lookahead(as_of = t), or a successful post_alert whose result carries
        # lookahead_used at the warning time (post_alert never ships without a current look-ahead; it computes one if the agent
        # had not asked). Either way the model received the look-ahead for t and must report it.
        la_from_posts = [(t['result'] or {}).get('lookahead_used') or {} for t in posts]
        la_statuses = [(t['result'] or {}).get('status') for t in la_results] + [l.get('status') for l in la_from_posts]
        la_unavail = any(st == 'prediction_unavailable' for st in la_statuses)
        la_at_t = (any(abs(float(json.loads(t['arguments']).get('as_of', -1e9)) - tx['t']) < 0.5 for t in la_results if t['arguments'])
                   or any(abs(float(l.get('as_of', -1e9)) - tx['t']) < 0.5 for l in la_from_posts))
        if posts and t_first is None:
            t_first = tx['t']; t_alert = wall
        summary = (final or '').strip()
        turns.append({'source_id': tx['source_id'], 't': tx['t'], 'wall_s': wall, 'calls': [c['name'] for c in rec['calls']],
                      'errors': rec['errors'], 'lookahead_at_t': la_at_t, 'lookahead_via_post_alert': bool(la_from_posts) and not la_results,
                      'lookahead_unavailable': la_unavail,
                      'unavailable_said': ('unavailable' in summary.lower()) if la_unavail else None,
                      'summary_ok': bool(summary) and 'exhausted' not in summary, 'posted': len(posts), 'summary': summary[:300]})
    # run-level scoring
    ingested = {}
    for eid, ev in base.events.items():
        if ev.get('_ingested'):
            src = ev.get('source_id') or (ev.get('source') or {}).get('id')
            ingested[src] = ingested.get(src, 0) + 1
    posted_sigs = sorted({wsig(w) for w in base.led.warnings if w['warning_id'] in loop.posted})
    st = dict(loop.stats)
    lat = sorted(st.pop('model_latency_s'))
    injected = len(tools.injected)
    checks = {
        'ingest_recall': round(sum(1 for s in ref['ingest_sources'] if ingested.get(s)) / max(1, len(ref['ingest_sources'])), 3),
        'ingest_once': all(v == 1 for v in ingested.values()),
        'alerts_exact': posted_sigs == ref['posted'],
        'extra_alerts': len(set(posted_sigs) - set(ref['posted'])),
        'lookahead_every_turn': all(t['lookahead_at_t'] for t in turns),
        'summary_every_turn': all(t['summary_ok'] for t in turns),
        'unavailable_reported': all(t['unavailable_said'] for t in turns if t['unavailable_said'] is not None),
        'no_refused_retries': st['unavailable_retries_refused'] == 0,
        'no_refused_reposts': st['reposts_refused'] == 0,
        'recovered_all_faults': st['recovered_after_error'] >= sum(1 for i in tools.injected if i['kind'] == 'transient'),
    }
    passed = (checks['ingest_recall'] == 1.0 and checks['ingest_once'] and checks['alerts_exact'] and checks['lookahead_every_turn']
              and checks['summary_every_turn'] and checks['unavailable_reported'] and checks['no_refused_retries']
              and checks['no_refused_reposts'] and checks['recovered_all_faults'] and st['rounds_exhausted'] == 0)
    invalid = st['tool_errors'] - sum(1 for i in tools.injected if i['kind'] == 'transient')
    return {'run_id': run_id, 'episode': episode, 'scenario': scenario, 'model': model, 'procedure': procedure, 'passed': passed, 'checks': checks,
            'stats': st, 'invalid_calls': max(0, invalid), 'injected_faults': injected,
            'injected_transient': sum(1 for i in tools.injected if i['kind'] == 'transient'),
            'model_latency_s': {'median': statistics.median(lat) if lat else None, 'p95': lat[int(0.95 * (len(lat) - 1))] if lat else None, 'n': len(lat)},
            'turn_wall_s_median': statistics.median([t['wall_s'] for t in turns]) if turns else None,
            'time_to_alert_s': t_alert, 'alert_turn_t': t_first, 'reference_posted': ref['posted'], 'posted': posted_sigs,
            'turns': turns, 'trace': str(trace)}


def summarize(runs):
    out = {}
    for sc in sorted({r['scenario'] for r in runs}):
        rs = [r for r in runs if r['scenario'] == sc]
        calls = sum(r['stats']['tool_calls'] for r in rs)
        inj = sum(r['injected_transient'] for r in rs)
        lat = [r['model_latency_s']['median'] for r in rs if r['model_latency_s']['median'] is not None]
        tta = [r['time_to_alert_s'] for r in rs if r['time_to_alert_s'] is not None]
        out[sc] = {'runs': len(rs), 'pass_rate': round(sum(r['passed'] for r in rs) / len(rs), 3),
                   'ingest_recall_mean': round(statistics.mean(r['checks']['ingest_recall'] for r in rs), 3),
                   'alerts_exact_rate': round(sum(r['checks']['alerts_exact'] for r in rs) / len(rs), 3),
                   'invalid_call_rate': round(sum(r['invalid_calls'] for r in rs) / max(1, calls), 3),
                   'recoveries': sum(r['stats']['recovered_after_error'] for r in rs), 'faults_injected': inj,
                   'refused_retries': sum(r['stats']['unavailable_retries_refused'] for r in rs),
                   'refused_reposts': sum(r['stats']['reposts_refused'] for r in rs),
                   'duplicates_ignored': sum(r['stats']['duplicates_ignored'] for r in rs),
                   'rounds_exhausted': sum(r['stats']['rounds_exhausted'] for r in rs),
                   'model_calls_per_turn': round(sum(r['stats']['model_calls'] for r in rs) / max(1, sum(r['stats']['turns'] for r in rs)), 2),
                   'model_latency_median_s': round(statistics.median(lat), 3) if lat else None,
                   'time_to_alert_median_s': round(statistics.median(tta), 3) if tta else None}
    return out


def markdown(model, summary):
    rows = ['| scenario | runs | pass | ingest recall | alerts exact | invalid calls | recoveries / faults | refused retries | model calls / turn | model latency med s | time to alert s |',
            '|---|---|---|---|---|---|---|---|---|---|---|']
    for sc, s in summary.items():
        rows.append(f"| {sc} | {s['runs']} | **{s['pass_rate']:.2f}** | {s['ingest_recall_mean']:.2f} | {s['alerts_exact_rate']:.2f} | {s['invalid_call_rate']:.3f} | "
                    f"{s['recoveries']} / {s['faults_injected']} | {s['refused_retries']} | {s['model_calls_per_turn']} | {s['model_latency_median_s']} | {s['time_to_alert_median_s']} |")
    return f'**{model}** (tool_loop_bench, SIMULATED E1)\n\n' + '\n'.join(rows)


# ---- in-process mock policies for the CPU self-check (no model) --------------------------------------------------------
class MockPolicy:
    """good: follows the procedure and retries a call whose last answer was an error; naive: never retries; stubborn: re-asks an
    unavailable lookahead. Enough to prove the bench separates the behaviours it claims to measure."""
    def __init__(self, mode):
        self.mode = mode

    def next(self, messages):   # noqa: C901
        tool_msgs = [m for m in messages if m['role'] == 'tool']
        names = [m['name'] for m in tool_msgs]
        results = [(m['name'], json.loads(m['content'])) for m in tool_msgs]
        user = next(m['content'] for m in messages if m['role'] == 'user')
        t = float(user.split('t=')[1].split(' ')[0]); src = user.split('source_id=')[1].split(',')[0]; text = user.split('"""')[1]

        def call(name, args):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'c{len(tool_msgs)}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}

        if self.mode == 'good3':   # the v3 envelope: alert turn = parse -> ingest -> post_alert; other turns = parse -> ingest -> lookahead
            if results and 'error' in results[-1][1]:
                prev = next(m for m in reversed(messages) if m['role'] == 'assistant' and m.get('tool_calls'))
                return {'role': 'assistant', 'content': None, 'tool_calls': prev['tool_calls']}
            okr = lambda n: [r for nm, r in results if nm == n and 'error' not in r]  # noqa: E731
            if not okr('parse_transmission'):
                return call('parse_transmission', {'text': text, 'airport': 'XSIM', 'source_id': src, 't': t})
            ev = okr('parse_transmission')[-1].get('event_id')
            if ev and not okr('ledger_ingest'):
                return call('ledger_ingest', {'event_id': ev})
            new_conf = [w for r in okr('ledger_ingest') for w in r.get('new_warnings', []) if w.get('severity') == 'CONFLICT']
            if new_conf and not okr('post_alert') and 'post_alert' not in names:
                return call('post_alert', {'warning_id': new_conf[0]['warning_id']})
            if okr('post_alert'):
                lu = okr('post_alert')[-1].get('lookahead_used') or {}
                unav = lu.get('status') == 'prediction_unavailable'
                return {'role': 'assistant', 'content': f'Recorded the transmission at t={t}. Posted warning {new_conf[0]["warning_id"]}. '
                        + ('Look-ahead unavailable at the warning time; that is not "no conflict".' if unav else 'Look-ahead ran at the warning time.')}
            if not okr('lookahead'):
                return call('lookahead', {'as_of': t})
            la = okr('lookahead')[-1]
            return {'role': 'assistant', 'content': f'Recorded the transmission at t={t}. ' + ('Look-ahead unavailable; that is not "no conflict".' if la.get('status') == 'prediction_unavailable' else 'Look-ahead ran.') + ' No new conflict warning.'}
        if results and 'error' in results[-1][1] and self.mode == 'good':
            prev = next(m for m in reversed(messages) if m['role'] == 'assistant' and m.get('tool_calls'))
            return {'role': 'assistant', 'content': None, 'tool_calls': prev['tool_calls']}          # retry the failed call once
        ok = lambda n: [r for nm, r in results if nm == n and 'error' not in r]  # noqa: E731
        if not ok('parse_transmission'):
            return call('parse_transmission', {'text': text, 'airport': 'XSIM', 'source_id': src, 't': t})
        ev = ok('parse_transmission')[-1].get('event_id')
        if ev and not ok('ledger_ingest') and not (self.mode == 'naive' and 'ledger_ingest' in names):
            return call('ledger_ingest', {'event_id': ev})
        if not ok('lookahead'):
            return call('lookahead', {'as_of': t})
        la = ok('lookahead')[-1]
        if self.mode == 'stubborn' and la.get('status') == 'prediction_unavailable' and names.count('lookahead') < 2:
            return call('lookahead', {'as_of': t})
        if not ok('open_warnings'):
            return call('open_warnings', {})
        ow = ok('open_warnings')[-1]
        conflicts = [w for w in ow.get('open_warnings', []) if w.get('severity') == 'CONFLICT']
        if self.mode != 'naive':   # the procedure: post only a CONFLICT that ledger_ingest reported as NEW in this turn
            new_ids = {w['warning_id'] for r in ok('ledger_ingest') for w in r.get('new_warnings', [])}
            conflicts = [w for w in conflicts if w['warning_id'] in new_ids]
        if conflicts and 'post_alert' not in names:
            return call('post_alert', {'warning_id': conflicts[0]['warning_id']})
        unavailable = la.get('status') == 'prediction_unavailable'
        return {'role': 'assistant', 'content': f'Recorded the transmission at t={t}. ' + ('Look-ahead unavailable; that is not "no conflict". ' if unavailable else 'Look-ahead ran. ')
                + (f'Posted warning {conflicts[0]["warning_id"]}.' if conflicts else 'No new conflict warning.')}


class _Handler(BaseHTTPRequestHandler):
    policy = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        msg = self.policy.next(body['messages'])
        data = json.dumps({'choices': [{'message': msg, 'finish_reason': 'tool_calls' if msg.get('tool_calls') else 'stop'}], 'usage': {'total_tokens': 1}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers()
        self.wfile.write(data)


def start_mock(mode):
    _Handler.policy = MockPolicy(mode)
    srv = HTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/v1'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--episode', default='SIM_E1_CROSS_DURING_ROLLOUT')
    ap.add_argument('--scenarios', default='positive,no_feed,transient,stale')
    ap.add_argument('--repeats', type=int, default=3)
    ap.add_argument('--base-url', default='http://127.0.0.1:8001/v1')
    ap.add_argument('--model', default='base')
    ap.add_argument('--max-rounds', type=int, default=8)
    ap.add_argument('--procedure', choices=sorted(TL.PROCEDURES), default='v6', help='system-prompt procedure given to the model (v1 = overnight five calls, v2 = alert right after ingest, v3 = alert turn ends at post_alert)')
    ap.add_argument('--mock', choices=['good', 'good3', 'naive', 'stubborn'], help='CPU self-check against an in-process scripted policy')
    ap.add_argument('--out', default=str(REPO / 'gym' / 'rollouts'))
    ap.add_argument('--run-id', default=time.strftime('%Y%m%d-%H%M%S'))
    a = ap.parse_args(argv)
    srv = None
    if a.mock:
        srv, a.base_url = start_mock(a.mock); a.model = f'mock-{a.mock}'
    out_dir = Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    for sc in a.scenarios.split(','):
        for i in range(a.repeats):
            rid = f'{a.run_id}_{sc}_{i}'
            r = run_once(a.episode, sc, a.base_url, a.model, out_dir, rid, a.max_rounds, a.procedure)
            runs.append(r)
            bad = {k: v for k, v in r['checks'].items() if v is False or (isinstance(v, float) and v < 1.0) or (k == 'extra_alerts' and v)}
            if r['stats']['rounds_exhausted']:
                bad['rounds_exhausted'] = r['stats']['rounds_exhausted']
            print(f"{sc:9s} run {i}: {'PASS' if r['passed'] else 'FAIL'}  calls={[t['calls'] for t in r['turns']]}  {bad}", flush=True)
    if srv:
        srv.shutdown()
    summary = summarize(runs)
    label = f'{a.model} [{a.procedure}]'
    res = {'SIMULATED': True, 'bench': 'venue/tool_loop_bench.py', 'model': label, 'procedure': a.procedure, 'base_url': a.base_url, 'episode': a.episode,
           'generated': time.strftime('%Y-%m-%dT%H:%M:%S'), 'summary': summary, 'runs': runs}
    path = out_dir / f"toolbench_{a.model.replace('/', '_')}_{a.procedure}_{a.run_id}.json"
    path.write_text(json.dumps(res, indent=1, default=str))
    print('\n' + markdown(label, summary) + f'\n\n-> {path}')
    return res


if __name__ == '__main__':
    main()
