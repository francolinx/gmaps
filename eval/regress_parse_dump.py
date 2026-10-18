"""Dump parser + ledger output on every SIM-1 transcript (regression baseline for parser/ledger changes).
  python3 eval/regress_parse_dump.py <repo root>
"""
import json, sys, glob
sys.path.insert(0, sys.argv[1] + '/lib')
from gmaps_core.parser import parse
from gmaps_core.ledger import Ledger
from gmaps_core.sim.simmap import SimAirport
out = {}
for tp in sorted(glob.glob(sys.argv[1] + '/episodes/*.transcript.json')):
    tr = json.load(open(tp)); ap = SimAirport(); led = Ledger(ap); rows = []
    for e in sorted(tr['events'], key=lambda e: e['t']):
        ev = parse(e['text_clean'], 'XSIM', speaker_hint='ATC' if e['speaker'] == 'ATC' else 'PILOT', resolver=ap, event_time=e['t'])
        led.ingest(ev, e['t']); led.tick(e['t'])
        rows.append({k: ev.get(k) for k in ('speech_act', 'kind', 'actor_candidates', 'runway', 'route', 'crossing_at', 'hold_constraints', 'hold_position')})
    out[tr['id']] = {'rows': rows, 'warnings': [(w['rule_id'], w['t_fired'], w['status']) for w in led.warnings]}
print(json.dumps(out, sort_keys=True))
