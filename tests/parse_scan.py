"""Parser scan over data/specs/scenarios.json.
Metric 1 (headline): transmissions the fixtures label safety-critical (clearances, crossings, holds, LUAW,
stops, go-arounds, taxi, crossing complete) - typed correctly with and without speaker hints.
Metric 2 (broad): every non-phone/non-surveillance line vs its fixture label (conversational lines are expected to
fall through to the residue path)."""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.parser import parse
from gmaps_core.airport import Airport
A = {'KLGA': Airport(ROOT / 'data/airports/klga.graph.json'), 'KBOS': Airport(ROOT / 'data/airports/kbos.graph.json')}
d = json.loads((ROOT / 'data/specs/scenarios.json').read_text())
CRIT = {'LANDING_CLEARANCE', 'TAKEOFF_CLEARANCE', 'LUAW', 'CROSS', 'HOLD_SHORT', 'CANCEL', 'GO_AROUND', 'TAXI', 'CROSSING_COMPLETE'}
rw = lambda x: '%02d' % int(x.rstrip('LRC')) + (x[-1] if x[-1] in 'LRC' else '')
for mode in ('hints', 'no hints'):
    n = ok_n = 0; misses = []
    for s in d['scenarios']:
        for e in s['events']:
            if e['freq'] in ('phone', 'adsb') or e['kind'] not in CRIT:
                continue
            n += 1
            h = ('ATC' if e['speaker'] == 'ATC' else 'PILOT') if mode == 'hints' else None
            ev = parse(e['text_clean'], s['airport'], speaker_hint=h, resolver=A[s['airport']])
            ok = ev['kind'] == e['kind'] or (e['kind'] == 'TAXI' and ev['kind'] in ('TAXI', 'HOLD_SHORT') and (ev['route'] or ev['hold_constraints']))
            if e.get('runway') and e['kind'] in ('LANDING_CLEARANCE', 'TAKEOFF_CLEARANCE', 'LUAW', 'CROSS') and ev['runway'] != rw(e['runway']):
                ok = False
            if e['kind'] == 'TAXI' and e.get('taxiways') and ev['route'] and ev['route'] != e['taxiways']:
                ok = False
            ok_n += ok
            if not ok:
                misses.append(f"  {s['id']} t={e['t']} {e['kind']} -> {ev['kind']} | {e['text_clean']}")
    print(f"safety-critical lines ({mode}): {ok_n}/{n} typed correctly")
    print('\n'.join(misses))
