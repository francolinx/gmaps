"""Synthetic scenario generator. SIMULATED: for the evaluation harness only.

  python3 eval/synthetic/generate.py --icao KATL --split dev                  # seed 9101, phrasing pool A
  python3 eval/synthetic/generate.py --icao KATL --split heldout --seed 7301  # fresh seed, phrasing pool B
  -> eval/synthetic/<split>/<icao>/*.scenario.json + *.truth.json
  then render.py (TTS + radio noise; held-out: other voices, harder noise) and asr_dir.py (Whisper), then eval/harness.py

Built from a built airport's identifiers and topology (eval/synthetic/chart_ids.py): NASR runways, chart taxiway names
(R1), which chart-named taxiway crosses which runway, hot spots. Half the scenarios carry a conflict (landing x crossing,
takeoff x crossing, line-up-and-wait x landing), half do not (crossing another runway, hold short, and the two section-8
cases: the landing aircraft's exit + ground taxi clearance, or its "clear of the runway" report, before the crossing).
The dev split is for fixing; the held-out split (new seed, other phrasings, voices and noise) is generated after the fixes
and only measured. Nothing positional is generated or used; no predictions on real geometry; results are SIMULATED.
"""
import argparse, json, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from chart_ids import ChartIdentifiers   # noqa: E402

DIGIT = {'0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'niner'}
NATO = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India Juliett Kilo Lima Mike November Oscar Papa Quebec Romeo Sierra Tango Uniform Victor Whiskey X-ray Yankee Zulu'.split()))
AIRLINES = [('United', 'UAL'), ('American', 'AAL'), ('JetBlue', 'JBU'), ('Southwest', 'SWA'), ('Spirit', 'NKS'), ('Delta', 'DAL'), ('Endeavor', 'EDV'), ('Frontier', 'FFT')]
SUF = {'L': 'left', 'R': 'right', 'C': 'center'}
TENS = {'1': 'ten', '2': 'twenty', '3': 'thirty', '4': 'forty', '5': 'fifty', '6': 'sixty', '7': 'seventy', '8': 'eighty', '9': 'ninety'}
TEENS = {'10': 'ten', '11': 'eleven', '12': 'twelve', '13': 'thirteen', '14': 'fourteen', '15': 'fifteen', '16': 'sixteen', '17': 'seventeen', '18': 'eighteen', '19': 'nineteen'}

# phrasing pools: A = dev (used for fixing), B = held-out (never seen while fixing)
PHRASES = {
    'A': {'land': ('{cs}, runway {rw}, cleared to land', 'Runway {rw}, cleared to land, {cs}'),
          'tkof': ('{cs}, runway {rw}, cleared for takeoff', 'Runway {rw}, cleared for takeoff, {cs}'),
          'luaw': ('{cs}, runway {rw}, line up and wait', 'Line up and wait runway {rw}, {cs}'),
          'cross': ('{cs}, cross runway {rw} at {tw}', 'Cross runway {rw} at {tw}, {cs}'),
          'hold': ('{cs}, hold short of runway {rw} at {tw}', 'Hold short of runway {rw} at {tw}, {cs}'),
          'exit': '{cs}, turn right at {tw}, contact ground point eight two', 'taxi': '{cs}, taxi to the ramp via {tw}',
          'clear': '{cs}, clear of runway {rw}'},
    'B': {'land': ('{cs}, cleared to land runway {rw}', 'Cleared to land {rw}, {cs}'),
          'tkof': ('{cs}, runway {rw}, wind two seven zero at eight, cleared for takeoff', 'Cleared for takeoff runway {rw}, {cs}'),
          'luaw': ('{cs}, line up and wait runway {rw}', 'Line up and wait {rw}, {cs}'),
          'cross': ('{cs}, at {tw} cross runway {rw}', 'Crossing runway {rw} at {tw}, {cs}'),
          'hold': ('{cs}, hold short runway {rw} at {tw}', 'Holding short runway {rw} at {tw}, {cs}'),
          'exit': '{cs}, left next taxiway {tw}, monitor ground point niner', 'taxi': '{cs}, taxi via {tw} to the ramp',
          'clear': '{cs} is clear of {rw}'},
}


def say_num(s):
    return ' '.join(DIGIT[c] for c in s)


def say_flight(s, grouped):
    """ATC says flight numbers digit by digit or in groups ('twelve twenty three'); the held-out pool mixes in groups."""
    if not grouped or len(s) not in (3, 4):
        return say_num(s)
    def pair(p):
        if p[0] == '0':
            return 'oh ' + DIGIT[p[1]]
        if p in TEENS:
            return TEENS[p]
        return TENS[p[0]] + ('' if p[1] == '0' else ' ' + DIGIT[p[1]].replace('niner', 'nine'))
    return (DIGIT[s[0]] + ' ' + pair(s[1:])) if len(s) == 3 else (pair(s[:2]) + ' ' + pair(s[2:]))


def say_rwy(end):
    m = re.fullmatch(r'(\d{2})([LRC]?)', end)
    return ' '.join([say_num(m.group(1)) if m.group(1)[0] != '0' else say_num(m.group(1)[1])] + ([SUF[m.group(2)]] if m.group(2) else []))


def say_twy(t):
    return ' '.join(NATO[c] if c.isalpha() else DIGIT[c] for c in t)


def norm_end(end):
    return end.lstrip('0') or '0'


class Gen:
    def __init__(self, A, rng, pool):
        self.A, self.rng, self.used, self.P = A, rng, set(), PHRASES[pool]
        self.grouped = pool == 'B'

    def callsign(self):
        while True:
            name, code = self.rng.choice(AIRLINES); num = str(self.rng.randint(11, 2999))
            if (code, num) not in self.used:
                self.used.add((code, num)); return f'{name} {say_flight(num, self.grouped and self.rng.random() < 0.5)}', code + num

    def tx(self, t, speaker, text, cs, kind, runway):
        return {'t': t, 'speaker': speaker, 'text': text}, {'fields': {'callsigns': [cs], 'runway': runway, 'instruction': kind}}

    def conv(self, t, cs_say, cs, kind, end, key, tw=None):
        rw = norm_end(end); atc, pilot = self.P[key]
        f = dict(cs=cs_say, rw=say_rwy(end), tw=say_twy(tw) if tw else '')
        return [self.tx(t, 'ATC', atc.format(**f) + '.', cs, kind, rw), self.tx(t + 4, 'PILOT', pilot.format(**f) + '.', cs, kind, rw)]


def runway_ends_of(A, rid):
    return sorted(e['identifier'] for e in A.ends.values() if e['runway_id'] == rid)


def build(icao, n, seed, split, pool):
    A = ChartIdentifiers(icao); rng = random.Random(seed); g = Gen(A, rng, pool)
    cross = sorted((tw, rid) for tw, rids in A.crossings.items() for rid in rids if re.fullmatch(r'[A-Z]{1,2}\d?', tw))
    no_cross = sorted(t for t in A.vocab if t not in A.crossings and re.fullmatch(r'[A-Z]{1,2}\d?', t))   # exit / taxi words: cross no runway (no R6)
    hs_tw = {tw for v in A.hot_spots.values() for tw in v}
    hot = [c for c in cross if c[0] in hs_tw]
    plan = ['C_LAND_CROSS', 'C_LAND_CROSS_HOTSPOT', 'C_TKOF_CROSS', 'C_TKOF_CROSS', 'C_LUAW_LAND',
            'N_CROSS_OTHER_RWY', 'N_CROSS_OTHER_RWY', 'N_HOLD_SHORT', 'N_EXIT_TAXI_THEN_CROSS', 'N_CLEAR_REPORT_THEN_CROSS'][:n]
    out = []
    for i, kind in enumerate(plan, 1):
        tw, rid = rng.choice(hot if kind.endswith('HOTSPOT') and hot else cross)
        end = rng.choice(runway_ends_of(A, rid))
        a_say, a = g.callsign(); b_say, b = g.callsign()
        L = []
        if kind in ('C_LAND_CROSS', 'C_LAND_CROSS_HOTSPOT', 'N_CROSS_OTHER_RWY', 'N_EXIT_TAXI_THEN_CROSS', 'N_CLEAR_REPORT_THEN_CROSS', 'N_HOLD_SHORT'):
            L += g.conv(0, a_say, a, 'LANDING_CLEARANCE', end, 'land')
        if kind == 'C_TKOF_CROSS':
            L += g.conv(0, a_say, a, 'TAKEOFF_CLEARANCE', end, 'tkof')
        if kind == 'C_LUAW_LAND':
            L += g.conv(0, a_say, a, 'LUAW', end, 'luaw')
            L += g.conv(14, b_say, b, 'LANDING_CLEARANCE', end, 'land')
        if kind in ('C_LAND_CROSS', 'C_LAND_CROSS_HOTSPOT', 'C_TKOF_CROSS'):
            L += g.conv(18, b_say, b, 'CROSS', end, 'cross', tw)
        if kind == 'N_CROSS_OTHER_RWY':
            other = [c for c in cross if c[1] != rid and tuple(sorted((c[1], rid))) not in A.intersecting_runways()] or [c for c in cross if c[1] != rid]
            tw2, rid2 = rng.choice(other); end2 = rng.choice(runway_ends_of(A, rid2))
            L += g.conv(18, b_say, b, 'CROSS', end2, 'cross', tw2)
        if kind == 'N_HOLD_SHORT':
            L += g.conv(18, b_say, b, 'HOLD_SHORT', end, 'hold', tw)
        if kind == 'N_EXIT_TAXI_THEN_CROSS':   # section 8: the exit alone keeps the landing; the ground taxi clearance releases it
            ex = rng.choice(no_cross)
            L += [g.tx(40, 'ATC', g.P['exit'].format(cs=a_say, tw=say_twy(ex)) + '.', a, 'TAXI', None)]
            L += [g.tx(48, 'ATC', g.P['taxi'].format(cs=a_say, tw=say_twy(ex)) + '.', a, 'TAXI', None)]
            L += g.conv(55, b_say, b, 'CROSS', end, 'cross', tw)
        if kind == 'N_CLEAR_REPORT_THEN_CROSS':   # section 8: the pilot's "clear of the runway" releases the landing
            L += [g.tx(45, 'PILOT', g.P['clear'].format(cs=a_say, rw=say_rwy(end)) + '.', a, 'CROSSING_COMPLETE', norm_end(end))]
            L += g.conv(55, b_say, b, 'CROSS', end, 'cross', tw)
        sid = f'SYN_{icao}_{split.upper()}_{i:02d}_{kind}'
        conflict = kind.startswith('C_')
        rule = {'C_LAND_CROSS': 'R1', 'C_LAND_CROSS_HOTSPOT': 'R1', 'C_TKOF_CROSS': 'R2', 'C_LUAW_LAND': 'R5'}.get(kind)
        txs = [dict(x[0], id=f'{sid}_{k:02d}') for k, x in enumerate(L)]
        out.append(({'id': sid, 'airport': icao, 'SIMULATED': True, 'split': split, 'phrasing_pool': pool, 'template': kind, 'generator': 'eval/synthetic/generate.py v2', 'seed': seed,
                     'context': {'runway': rid.split(':')[-1], 'taxiway': tw, 'hot_spot': next((h for h, v in A.hot_spots.items() if tw in v), None)},
                     'note': 'Synthetic phraseology from chart identifiers; no positions; no predictions on real geometry. Evaluation harness only.',
                     'transmissions': txs},
                    {'id': sid, 'EVALUATION_ONLY': True, 'expected_alert': conflict, 'expected_rules': [rule] if rule else [], 'conflict_t': None,
                     'section8_case': kind in ('N_EXIT_TAXI_THEN_CROSS', 'N_CLEAR_REPORT_THEN_CROSS'),
                     'transmissions': [dict(x[1], id=f'{sid}_{k:02d}') for k, x in enumerate(L)]}))
    d = ROOT / 'eval/synthetic' / split / icao.lower(); d.mkdir(parents=True, exist_ok=True)
    for sc, tr in out:
        (d / f"{sc['id']}.scenario.json").write_text(json.dumps(sc, indent=1) + '\n'); (d / f"{sc['id']}.truth.json").write_text(json.dumps(tr, indent=1) + '\n')
    print(f'{icao} {split}: {len(out)} scenarios -> {d.relative_to(ROOT)} ({sum(1 for _, t in out if t["expected_alert"])} with a conflict; phrasing {pool}; seed {seed})')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); ap.add_argument('--n', type=int, default=10)
    ap.add_argument('--split', choices=('dev', 'heldout'), default='dev'); ap.add_argument('--seed', type=int)
    a = ap.parse_args()
    build(a.icao.upper(), a.n, a.seed if a.seed is not None else 9101, a.split, 'A' if a.split == 'dev' else 'B')
