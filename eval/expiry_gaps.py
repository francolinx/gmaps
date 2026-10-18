"""N7 TARGETED EXPIRY TESTS: cases that actually exercise reservation expiry, on the fictional SIM-1 map.

  python3 eval/expiry_gaps.py        # prints the table, writes eval/results/expiry_gaps_2026-10-10.json, exit 1 on a violation

Each case is a first authorization, then a conflicting second one `gap` minutes later, run through the runtime ledger configured
from data/specs/runtime_config.json (soft expiry, hard limit) on a 1 s clock. It reports what the second authorization raises:
  ALERT     a CONFLICT/CAUTION warning (scored as an alert);
  ADVISORY  R17 "possible conflict with an unverified earlier clearance" (category unverified_conflict, scored on its own);
  nothing   neither.
Requirement checked: nothing goes silent before the hard limit (every gap below it raises an ALERT or an ADVISORY), and every
drop after it is logged (hard_drop_without_release). SIMULATED, fictional map; evaluation only.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib')]
from gmaps_core.parser import parse                  # noqa: E402
from gmaps_core.sim.simmap import SimAirport         # noqa: E402
from gmaps_core.ledger import Ledger               # noqa: E402

CFG = json.loads((ROOT / 'data/specs/runtime_config.json').read_text())
GAPS_MIN = (2, 4, 6, 8, 12, 25)
LAND = 'SimAir two one two, runway three six, cleared to land.'
CROSS = 'Rescue seven, cross runway three six at Kilo.'
CROSS_RB = 'Cross runway three six at Kilo, Rescue seven.'
LUAW = 'SimAir four five six, runway three six, line up and wait.'
LUAW_RB = 'Runway three six, line up and wait, SimAir four five six.'
LAND_B = 'SimAir seven eight nine, runway three six, cleared to land.'
STOP = 'Rescue seven, stop, hold position.'
CASES = {   # name: (first lines [(dt, speaker, text)], second line); the second line comes `gap` after t=0
    'landing cleared, then a crossing': ([(0, 'ATC', LAND)], ('ATC', CROSS)),
    'line up and wait (read back), then a landing on the same runway': ([(0, 'ATC', LUAW), (4, 'PILOT', LUAW_RB)], ('ATC', LAND_B)),
    'line up and wait (no readback heard), then a landing on the same runway': ([(0, 'ATC', LUAW)], ('ATC', LAND_B)),
    'crossing read back, then a landing': ([(0, 'ATC', CROSS), (4, 'PILOT', CROSS_RB)], ('ATC', LAND)),
    'crossing never read back (exempt from soft expiry), then a landing': ([(0, 'ATC', CROSS)], ('ATC', LAND)),
    'crossing read back, stop never acknowledged (exempt), then a landing': ([(0, 'ATC', CROSS), (4, 'PILOT', CROSS_RB), (20, 'ATC', STOP)], ('ATC', LAND)),
}
ALERT = {'CONFLICT', 'CAUTION'}
# controls: a release IS heard before the second authorization, so 'nothing' is the right answer at every gap
CLEAR = 'SimAir two one two clear of runway three six.'
CONTROLS = {'landing cleared, clear of the runway reported at 90 s, then a crossing': ([(0, 'ATC', LAND), (90, 'PILOT', CLEAR)], ('ATC', CROSS))}


def run_case(first, second, gap_s, cfg=CFG):
    ap = SimAirport()
    led = Ledger(ap, expiry=cfg['reservation_expiry_s'], hard_limit_s=cfg.get('reservation_hard_limit_s'), max_open_warnings=cfg['max_open_warnings'])
    lines = sorted(first + [(gap_s, *second)], key=lambda x: x[0])
    for t, spk, txt in lines:
        for k in range(int(led.now or 0) + 1, t + 1):   # the deployed 1 s clock
            led.tick(k)
        n0 = len(led.warnings)
        led.ingest(parse(txt, 'XSIM', speaker_hint=spk, resolver=ap, event_time=t), t)
        if t == gap_s:   # what the second authorization raised: new warnings, or an open one it updated in place (dedup)
            got = led.warnings[n0:] + [w for w in led.warnings[:n0] if w.get('t_updated') == gap_s]
    out = 'ALERT' if any(w['severity'] in ALERT for w in got) else 'ADVISORY' if any(w['rule_id'] == 'R17' for w in got) else 'nothing'
    return {'gap_s': gap_s, 'raised': out, 'rules': sorted({f"{w['rule_id']} {w['severity']}" + (f" (underlying {w['underlying_rule']})" if w.get('underlying_rule') else '') for w in got}),
            'expiry_log': [f"{e['kind']} {e['callsign']} {e['type']} t={e['t']}" for e in led.expiries],
            'notes_exempt': [n['note'] for n in led.notes if 'NOT soft-expired' in n['note']][:1]}


def main():
    hard = CFG.get('reservation_hard_limit_s')
    res, bad = {}, []
    for name, (first, second) in CASES.items():
        rows = [run_case(first, second, g * 60) for g in GAPS_MIN]
        res[name] = rows
        for r in rows:
            silent = r['raised'] == 'nothing' and (hard is None or r['gap_s'] < hard)
            unlogged = r['raised'] == 'nothing' and not any(x.startswith('hard_drop_without_release') for x in r['expiry_log'])
            if silent or unlogged:
                bad.append([name, r['gap_s'], r['raised']])
    ctl = {}
    for name, (first, second) in CONTROLS.items():
        ctl[name] = [run_case(first, second, g * 60) for g in GAPS_MIN]
        bad += [[name, r['gap_s'], r['raised']] for r in ctl[name] if r['raised'] != 'nothing']
    out = {'note': 'N7 targeted expiry tests, fictional SIM-1 map (SIMULATED). raised = what the SECOND authorization raises.',
           'config': {k: CFG.get(k) for k in ('reservation_expiry_s', 'reservation_hard_limit_s', 'max_open_warnings')},
           'gaps_min': list(GAPS_MIN), 'cases': res, 'controls (release heard; nothing expected)': ctl, 'violations_silent_before_hard_limit_or_unlogged_drop': bad, 'pass': not bad}
    (ROOT / 'eval/results/expiry_gaps_2026-10-10.json').write_text(json.dumps(out, indent=1) + '\n')
    print('gap (min):'.ljust(76) + ''.join(f'{g:>10}' for g in GAPS_MIN))
    for name, rows in res.items():
        print(name.ljust(76) + ''.join(f"{r['raised']:>10}" for r in rows))
    for name, rows in ctl.items():
        print(('control: ' + name).ljust(76) + ''.join(f"{r['raised']:>10}" for r in rows))
    print('PASS' if not bad else f'FAIL {bad}')
    sys.exit(0 if not bad else 1)


if __name__ == '__main__':
    main()
