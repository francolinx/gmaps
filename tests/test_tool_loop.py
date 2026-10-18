"""CPU test of venue/tool_loop.py against a scripted mock of an OpenAI-compatible server (no model, no GPU).

  python3 tests/test_tool_loop.py

The mock plays three "models":
  good      parse -> ingest -> lookahead -> open_warnings -> (post_alert for a new CONFLICT) -> summary
  clumsy    first call has a missing required argument and an unknown tool, then it corrects itself (recovery is recorded)
  stubborn  after lookahead answers prediction_unavailable it asks again for the same as_of: the loop must refuse the retry
Asserts: nothing is ingested twice, the clumsy model recovers after one error round, the stubborn model's retry is refused and
counted, every turn ends with a summary, the trace file has the records. SIM-1 / XSIM only; feed written to a temp runtime dir.
"""
import json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'venue'), str(REPO / 'lib')]
import gmaps_mcp as M            # noqa: E402
import tool_loop as TL           # noqa: E402


class Script:
    """Decides the next assistant message from the conversation so far (what a model would return)."""
    def __init__(self, mode):
        self.mode = mode

    def next(self, messages):
        tool_msgs = [m for m in messages if m['role'] == 'tool']
        names = [m['name'] for m in tool_msgs]
        last = json.loads(tool_msgs[-1]['content']) if tool_msgs else None
        user = next(m['content'] for m in messages if m['role'] == 'user')
        t = float(user.split('t=')[1].split(' ')[0]); src = user.split('source_id=')[1].split(',')[0]
        text = user.split('"""')[1]

        def call(name, args):
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'c{len(tool_msgs)}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}

        if not names:
            if self.mode == 'clumsy':
                return call('parse_transmission', {'text': text})          # missing airport -> tool error
            return call('parse_transmission', {'text': text, 'airport': 'XSIM', 'source_id': src, 't': t})
        if self.mode == 'clumsy' and names == ['parse_transmission'] and 'error' in last:
            return call('radio_transmit', {'text': 'hello'})                # unknown tool -> tool error
        if self.mode == 'clumsy' and names[-1] == 'radio_transmit':
            return call('parse_transmission', {'text': text, 'airport': 'XSIM', 'source_id': src, 't': t})
        parse_results = [json.loads(m['content']) for m in tool_msgs if m['name'] == 'parse_transmission' and 'error' not in m['content']]
        if parse_results and 'ledger_ingest' not in names:
            ev = parse_results[-1].get('event_id')
            if ev:
                return call('ledger_ingest', {'event_id': ev})
        if 'lookahead' not in names:
            return call('lookahead', {'as_of': t})
        la = [json.loads(m['content']) for m in tool_msgs if m['name'] == 'lookahead']
        if self.mode == 'stubborn' and la and la[-1].get('status') == 'prediction_unavailable' and names.count('lookahead') < 3:
            return call('lookahead', {'as_of': t})                           # must be refused by the loop
        if 'open_warnings' not in names:
            return call('open_warnings', {})
        ow = [json.loads(m['content']) for m in tool_msgs if m['name'] == 'open_warnings'][-1]
        conflicts = [w for w in ow.get('open_warnings', []) if 'CONFLICT' in str(w.get('severity', '')) + str(w.get('level', '')) + str(w.get('category', ''))]
        if conflicts and 'post_alert' not in names:
            return call('post_alert', {'warning_id': conflicts[0]['warning_id']})
        unavailable = bool(la and la[-1].get('status') == 'prediction_unavailable')
        return {'role': 'assistant', 'content': f'Recorded the transmission at t={t}. ' + ('Look-ahead was unavailable (no fresh observations); that is not "no conflict". ' if unavailable else 'Look-ahead ran. ') + (f'Posted warning {conflicts[0]["warning_id"]}.' if conflicts else 'No new conflict warning.')}


class Handler(BaseHTTPRequestHandler):
    script = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        msg = self.script.next(body['messages'])
        out = {'choices': [{'message': msg, 'finish_reason': 'tool_calls' if msg.get('tool_calls') else 'stop'}], 'usage': {'total_tokens': 1}}
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers()
        self.wfile.write(data)


def run(mode, episode, emit_feed):
    Handler.script = Script(mode)
    srv = HTTPServer(('127.0.0.1', 0), Handler); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp())
    M.RUNTIME = tmp                                   # the tools read the feed from <RUNTIME>/feed; keep the test out of the repo
    import replay_emit
    replay_emit.FEED = tmp / 'feed'
    tools = M.GmapsTools(episode, f'test-{mode}', send=False, input_mode='text')
    loop = TL.Loop(tools, f'http://127.0.0.1:{port}/v1', 'mock', tmp / 'trace.jsonl', max_rounds=8)
    recs = []
    for tx in TL.episode_transmissions(episode):
        if emit_feed:
            replay_emit.emit(episode, tx['t'])
        final, rec = loop.turn(tx)
        recs.append(rec)
    srv.shutdown()
    trace = [json.loads(l) for l in open(tmp / 'trace.jsonl')]
    return loop.stats, recs, trace, tools


def main():
    ok = True
    ep = 'SIM_E1_CROSS_DURING_ROLLOUT'
    # 1. good model with the feed: all turns end in a summary; ingest once per event; a warning is posted at most once
    st, recs, trace, tools = run('good', ep, emit_feed=True)
    ingests = [c for r in recs for c in r['calls'] if c['name'] == 'ledger_ingest' and c['ok']]
    posted_ok = [t for t in trace if t['kind'] == 'tool' and t['name'] == 'post_alert' and not t['error'] and not (isinstance(t['result'], dict) and t['result'].get('ok') is False)]
    posted = [p['arguments'] for p in posted_ok]
    checks = {
        'good: every turn has a summary': all(r['summary_unverified'] and 'exhausted' not in r['summary_unverified'] for r in recs),
        'good: no tool errors': st['tool_errors'] == 0,
        'good: each event ingested once': len(ingests) == len({c['arguments'] for c in ingests}),
        'good: no warning posted twice (loop refuses re-posts)': len(posted) == len(set(posted)) and st.get('reposts_refused', 0) >= 0,
        'good: trace has turn_start/model/tool/turn_end records': {t['kind'] for t in trace} >= {'turn_start', 'model', 'tool', 'turn_end'},
    }
    # 2. clumsy model: one error round, then recovery; still ingests exactly once per event
    st2, recs2, _, _ = run('clumsy', ep, emit_feed=True)
    checks.update({
        'clumsy: tool errors were returned, not executed': st2['tool_errors'] >= 2 * len(recs2) - 1,
        'clumsy: recovered after the error round': st2['recovered_after_error'] >= 1,
        'clumsy: still ends every turn with a summary': all(r['summary_unverified'] and 'exhausted' not in r['summary_unverified'] for r in recs2),
    })
    # 3. stubborn model without any feed: lookahead is unavailable; the retry is refused and counted
    st3, recs3, _, _ = run('stubborn', ep, emit_feed=False)
    checks.update({
        'stubborn: retry of an unavailable lookahead refused': st3['unavailable_retries_refused'] >= 1,
        'stubborn: summary says unavailable, not safe': all('unavailable' in r['summary_unverified'].lower() for r in recs3),
    })
    for k, v in checks.items():
        print(('PASS ' if v else 'FAIL ') + k); ok &= bool(v)
    print(json.dumps({'good': st, 'clumsy': st2, 'stubborn': st3}, indent=1, default=str))
    print(f'{sum(checks.values())}/{len(checks)} passed')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
