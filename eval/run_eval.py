"""EVALUATION RUNNER (prepared). The only code that reads eval/labels. Scores three methods on the same inputs:
  route_only  - ledger authorization rules only (no motion)
  motion_only - look-ahead from observations + map, no instruction intent
  combined    - GMAPS: instruction intent + observations + map
A method 'raises' when a candidate is present for 2 consecutive ticks (declared persistence rule).
Reference time = truth encounter start, else the time the vehicle passes the hold line (if a hold was issued).
Outcomes: TP (raised before reference), LATE (raised after), MISS, FP (raised when should not), TN.
These are results on FICTIONAL episodes with a declared model; they are not operational performance claims.

usage: python3 eval/run_eval.py [EPISODE_ID ...]   (default: all episodes that have labels)
"""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools')); sys.path.insert(0, str(ROOT / 'lib'))
import run_sim_episode as R


def first_persistent(flags, ts, k=2):
    run = 0
    for f, t in zip(flags, ts):
        run = run + 1 if f else 0
        if run >= k:
            return ts[ts.index(t) - k + 1]
    return None


def score(eid):
    lab = json.loads((ROOT / f'eval/labels/{eid}.labels.json').read_text())
    res = R.run(eid)
    ts = [f['t'] for f in res['frames']]
    flags = {
        'route_only': [f['route_only_raised'] for f in res['frames']],
        'motion_only': [any(c['raised'] for c in f['motion_only']['candidates']) for f in res['frames']],
        'combined': [any(c['raised'] for c in f['combined']['candidates']) for f in res['frames']],
    }
    ref = (lab['truth_encounter_interval'] or [None])[0]
    if ref is None and lab['vehicle_passes_hold_line_at'] is not None and lab['should_raise_candidate']:
        ref = lab['vehicle_passes_hold_line_at']
    row = {'episode': eid, 'should_raise': lab['should_raise_candidate'], 'reference_t': ref}
    for m, fl in flags.items():
        t = first_persistent(fl, ts)
        if lab['should_raise_candidate']:
            out = 'MISS' if t is None else ('TP' if t < ref else 'LATE')
            lead = None if t is None else round(ref - t, 1)
        else:
            out, lead = ('FP' if t is not None else 'TN'), None
        row[m] = {'outcome': out, 'first_raise_t': t, 'lead_s': lead, 'raised_ticks': sum(fl)}
    unavail = [f['t'] for f in res['frames'] if any(u['track'] == 'T-VH7' for u in f['combined']['unavailable']) and f['t'] > 25]
    row['combined_withdrawn_ticks'] = len(unavail)
    led_rules = sorted({w['rule_id'] for w in res['ledger']['warnings']})
    row['ledger_rules'] = led_rules
    row['ledger_rules_expected'] = lab['expected_ledger_rules']
    row['ledger_rules_match'] = set(lab['expected_ledger_rules']) <= set(led_rules)
    return row


if __name__ == '__main__':
    ids = sys.argv[1:] or sorted(p.name.replace('.labels.json', '') for p in (ROOT / 'eval/labels').glob('*.labels.json'))
    rows = [score(e) for e in ids]
    print(f"{'episode':32} {'raise?':6} {'ref_t':>6} | {'route_only':18} | {'motion_only':18} | {'combined':18} | ledger")
    for r in rows:
        cell = lambda m: f"{r[m]['outcome']:4} t={str(r[m]['first_raise_t']):>4} lead={str(r[m]['lead_s']):>5}"
        print(f"{r['episode'][:32]:32} {str(r['should_raise']):6} {str(r['reference_t']):>6} | {cell('route_only'):18} | {cell('motion_only'):18} | {cell('combined'):18} | {r['ledger_rules']} {'ok' if r['ledger_rules_match'] else 'MISMATCH'}")
    tally = {m: {} for m in ('route_only', 'motion_only', 'combined')}
    for r in rows:
        for m in tally:
            tally[m][r[m]['outcome']] = tally[m].get(r[m]['outcome'], 0) + 1
    print('tally', json.dumps(tally))
    (ROOT / 'eval/results_latest.json').write_text(json.dumps({'rows': rows, 'tally': tally,
        'note': 'Fictional episodes, declared model; not operational performance.'}, indent=1))
