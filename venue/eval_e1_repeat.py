"""Score E1 demo runs against criteria fixed before running (Spark re-run, criteria from STATUS §11).
  python3 venue/eval_e1_repeat.py RUN_ID [RUN_ID ...]
Pass: 5 turns rc=0, each with tool calls on its own run server; all input via Whisper; R1 w0001 at t=28; exactly one post_alert (w0001, tier POSSIBLE_CONFLICT)
whose look-ahead is as_of 28 with a raised overlap.
clip_to_alert = Whisper seconds for the t=28 clip + (post_alert call completed, server clock - t=28 agent turn start).
The alert is a dry run (Telegram removed after the event), so unlike there is no delivery time in it.
Reads only the agent/tool logs in ~/gmaps_venue/log; never eval/ truth or labels.
"""
import json, statistics, sys
from pathlib import Path

LOG = Path.home() / 'gmaps_venue' / 'log'


def jl(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def score(run):
    turns = jl(LOG / f'agent_turns_{run}.jsonl')
    p = LOG / f'mcp_calls_{run}.jsonl'
    calls = [c for c in jl(p) if c.get('tool')] if p.exists() else []
    fails = [] if p.exists() else ['no tool-call log for this run (its server received no calls)']
    if len(turns) != 5 or any(t['rc'] != 0 for t in turns):
        fails.append(f"turns={len(turns)} rc={[t['rc'] for t in turns]}")
    if any(t['input_source'] != 'whisper' for t in turns):
        fails.append(f"input={[t['input_source'] for t in turns]}")
    # every turn's tool calls must reach THIS run's server inside the turn (catches overlapping runs swapping servers)
    stray = [t['t'] for t in turns if not any(t['epoch_start'] <= c['t'] <= t['epoch_start'] + t['wall_s'] for c in calls)]
    if stray:
        fails.append(f'turns with no tool call on this run server: t={stray}')
    ing = [c['result'] for c in calls if c['tool'] == 'ledger_ingest' and c.get('ok')] + \
          [c['result']['ingest'] for c in calls if c['tool'] in ('fast_path', 'process_transmission') and c.get('ok') and (c.get('result') or {}).get('ingest')
           and not (c.get('result') or {}).get('note', '').startswith('already processed')]
    new = [(w['rule_id'], w['warning_id'], w['t_fired']) for r in ing for w in r.get('new_warnings', [])]
    if ('R1', 'w0001', 28) not in [(r, i, int(t)) for r, i, t in new]:
        fails.append(f'warnings={new}')
    alerts = [c for c in calls if c['tool'] == 'post_alert']
    ok_alerts = [c for c in alerts if c.get('ok') and (c.get('result') or {}).get('status') != 'already_posted']
    la, a = None, None
    if len(ok_alerts) != 1:
        fails.append(f'post_alert ok={len(ok_alerts)} total={len(alerts)}')
    else:
        a = ok_alerts[0]
        la = a['result'].get('lookahead_used') or {}
        raised = [x for x in la.get('candidates', []) if x.get('raised') and x.get('type') == 'occupancy_overlap']
        if a['result']['warning_id'] != 'w0001' or a['result'].get('tier') != 'POSSIBLE_CONFLICT':
            fails.append(f"alert {a['result']['warning_id']} tier={a['result'].get('tier')}")
        if la.get('as_of') != 28.0 or not raised:
            fails.append(f"alert look-ahead as_of={la.get('as_of')} raised={len(raised)}")
    t28 = next((t for t in turns if t['t'] == 28), None)
    c2a, path = None, None
    if a and t28 and t28.get('asr_s') is not None:
        fp = t28.get('fast_path') or {}
        via = a.get('via') or 'agent post_alert'
        start = fp['epoch_start'] if via == 'host fast path' and fp.get('epoch_start') else t28['epoch_start']   # end of transmission -> alert: Whisper + this path
        c2a = round(t28['asr_s'] + (a['t'] + a['ms'] / 1000 - start), 2); path = via
    ov = next((x for x in (la or {}).get('candidates', []) if x.get('raised')), {})
    return {'run': run, 'pass': not fails, 'fails': fails, 'clip_to_alert_s': c2a, 'alert_path': path,
            'asr_t28_s': t28 and t28.get('asr_s'), 'overlap': ov.get('overlap'), 'lead_s': ov.get('lead_s'),
            'lookahead_source': a and a['result'].get('lookahead_source'), 'alert_status': a and a['result'].get('status'),
            'turn_wall_s': [t['wall_s'] for t in turns]}


if __name__ == '__main__':
    rs = [score(r) for r in sys.argv[1:]]
    for i, r in enumerate(rs, 1):
        print(f"run {i} {r['run']}: {'PASS' if r['pass'] else 'FAIL ' + '; '.join(r['fails'])} clip_to_alert={r['clip_to_alert_s']}s via={r['alert_path']} "
              f"(asr {r['asr_t28_s']}) overlap={r['overlap']} lead={r['lead_s']} la_source={r['lookahead_source']} "
              f"alert={r['alert_status']} turn_wall={r['turn_wall_s']}")
    v = [r['clip_to_alert_s'] for r in rs if r['clip_to_alert_s'] is not None]
    if v:
        print(f'clip_to_alert values (n={len(v)}): {v} median {statistics.median(v)}')
