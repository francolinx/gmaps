"""Synthetic scenario generator v4 = v3 + round 3 (the LAST synthetic tuning round). Derived from generate_v3.py
(kept unchanged so the v3 sets stay reproducible). Differences: dev4 draws each element from pools C or D (both seen by now);
held-out4 uses pool E (new 7110.65 realizations); two readback-mismatch scenarios (R14, scored separately); and a traffic file
per scenario (callsigns on frequency: the scenario aircraft + 8 others; 'realistic' drops ~15 % and adds 3 not on frequency).

  python3 eval/synthetic/generate_v4.py --icao KATL --split dev4 --seed 9301
  python3 eval/synthetic/generate_v4.py --icao KATL --split heldout4 --seed 7501      # generated after the round-3 fixes are frozen

--- v3 docstring ---
Synthetic scenario generator v3: phrasings from FAA JO 7110.65BB (eval/synthetic/phraseology_7110.json,
every element cited), not from the GMAPS grammar. SIMULATED: for the evaluation harness only.

  python3 eval/synthetic/generate_v3.py --icao KATL --split dev3 --seed 9201       # phrasing pool C (fixing)
  python3 eval/synthetic/generate_v3.py --icao KATL --split heldout3 --seed 7401   # pool D, generated after the fixes
  -> eval/synthetic/<split>/<icao>/*.scenario.json + *.truth.json; then render.py, asr_dir.py, eval/harness.py, eval/e2e.py

Identifiers and topology from a built airport (chart_ids.py; no positions). 14 scenario types per airport (7 with an
authorization conflict or open obligation, 7 without), listed with their 7110.65 basis in the catalogue. Pools C and D
use disjoint realizations of the same 7110.65 elements (shared only where the order gives a single form).
"""
import argparse, json, random, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from chart_ids import ChartIdentifiers   # noqa: E402
from generate import DIGIT, NATO, AIRLINES, TENS, TEENS, say_num, say_rwy, say_twy, norm_end, runway_ends_of   # noqa: E402

CAT = json.loads((Path(__file__).parent / 'phraseology_7110.json').read_text())
POOL = {'dev4': ['C', 'D'], 'heldout4': ['E']}
TYPES = ['Boeing seven thirty seven', 'Airbus three twenty', 'Embraer one seventy five', 'regional jet', 'Boeing seven fifty seven']
CIVIL_TYPES = ['Bonanza', 'Cessna', 'Citation']
PLAN_V4_EXTRA = ['RB_WRONG_RUNWAY', 'RB_MISSING_RUNWAY']
PLAN = ['C_LAND_CROSS', 'C_LAND_CROSS_HOTSPOT', 'C_TKOF_CROSS', 'C_LUAW_LAND', 'C_TKOF_LAND_SAME', 'C_TKOF_LAND_INTERSECT', 'C_HOLD_NO_READBACK',
        'N_CROSS_OTHER_RWY', 'N_HOLD_SHORT', 'N_EXIT_TAXI_THEN_CROSS', 'N_CLEAR_REPORT_THEN_CROSS', 'N_GO_AROUND_THEN_CROSS', 'N_LUAW_THEN_TKOF', 'N_LUAW_CONTINUE']
RULE = {'C_LAND_CROSS': 'R1', 'C_LAND_CROSS_HOTSPOT': 'R1', 'C_TKOF_CROSS': 'R2', 'C_LUAW_LAND': 'R5', 'C_TKOF_LAND_SAME': 'R3', 'C_TKOF_LAND_INTERSECT': 'R4', 'C_HOLD_NO_READBACK': 'R7'}


def say_pair(p):   # '23' -> 'twenty three', '04' -> 'oh four', '15' -> 'fifteen', '30' -> 'thirty'
    if p[0] == '0':
        return 'oh ' + DIGIT[p[1]].replace('niner', 'nine')
    if p in TEENS:
        return TEENS[p]
    return TENS[p[0]] + ('' if p[1] == '0' else ' ' + DIGIT[p[1]].replace('niner', 'nine'))


class Gen:
    def __init__(self, rng, pools):
        self.rng, self.pools, self.used = rng, pools, set()

    def p(self):
        return self.rng.choice(self.pools) if len(self.pools) > 1 else self.pools[0]

    @property
    def cs_cat(self):
        return CAT['callsign'][self.p()]

    def flight(self):
        """(spoken, digits) in this pool's 2-4-20a2 form."""
        r, style = self.rng, self.rng.choice(self.cs_cat['styles'])
        if style == 'digits':
            n = str(r.randint(101, 999)); return say_num(n), n
        if style == 'pairs':       # four digits as two pairs: 'twelve thirty four', 'twenty one oh five' (not 10xx: pool D's)
            n = f'{r.randint(11, 29)}{r.randint(0, 99):02d}'; return f'{say_pair(n[:2])} {say_pair(n[2:])}', n
        if style == 'group3':      # 'four ninety two'
            n = f'{r.randint(2, 9)}{r.randint(11, 99)}'; return f'{DIGIT[n[0]]} {say_pair(n[1:])}', n
        if style == 'hundred':     # 'one hundred', 'two hundred five'
            h, u = r.randint(1, 9), r.choice([0, 0, r.randint(1, 9)])
            return f"{DIGIT[str(h)].replace('niner', 'nine')} hundred" + (f" {DIGIT[str(u)].replace('niner', 'nine')}" if u else ''), str(h * 100 + u)
        if style == 'pairs_ten':   # 'ten forty two'
            n = f'10{r.randint(11, 99)}'; return f'ten {say_pair(n[2:])}', n
        if style == 'hundreds4':   # pool E: 'twelve hundred', 'twenty one hundred'
            n = f'{r.randint(11, 29)}00'; return f'{say_pair(n[:2])} hundred', n
        if style == 'digits4':     # pool E: four digits spoken singly
            n = str(r.randint(1000, 9999)); return say_num(n), n
        raise ValueError(style)

    def callsign(self):
        while True:
            cat = self.cs_cat
            if self.rng.random() < CAT['callsign']['civil_rate']:
                d = str(self.rng.randint(100, 9999)); s = ''.join(self.rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(self.rng.choice((1, 2))))
                s = s[:max(0, 5 - len(d))]   # a U.S. registration has at most 5 characters after the N
                lead = {'november': 'November', 'twin': 'Twin Cessna'}.get(cat['civil']) or self.rng.choice(CIVIL_TYPES)
                say, code = f"{lead} {say_num(d)} {' '.join(NATO[c] for c in s)}", f'N{d}{s}'
            else:
                name, icao = self.rng.choice(AIRLINES); fs, n = self.flight(); say, code = f'{name} {fs}', icao + n
            if code not in self.used:
                self.used.add(code); return say, code

    def wind(self):
        d = f'{self.rng.randint(1, 36) * 10:03d}'; s = self.rng.randint(4, 22)
        f = dict(dir=say_num(d), spd=say_num(str(s)), gust=say_num(str(s + self.rng.randint(8, 15))))
        pool = self.p()
        key = self.rng.choice([pool, pool + '_alt']) if pool + '_alt' in CAT['wind'] else pool
        return CAT['wind'][key].format(**f)

    def atc(self, key, **f):
        v = CAT['atc'][key]
        opts = [v['shared']] if 'shared' in v else v[self.p()]
        return self.rng.choice(opts).format(**f)

    def fill(self, cs, end=None, tw=None, **kw):
        kw.setdefault('rw2', say_rwy(self.rng.choice(self.other_ends)) if self.other_ends else '')
        return dict(cs=cs, rw=say_rwy(end) if end else '', tw=say_twy(tw) if tw else '', wind=self.wind(), type=self.rng.choice(TYPES),
                    miles=self.rng.choice(['four', 'five', 'six']), dir=self.rng.choice(['left', 'right']), **kw)


def tx(t, speaker, text, cs, kind, end):
    return {'t': t, 'speaker': speaker, 'text': text[0].upper() + text[1:]}, {'fields': {'callsigns': [cs], 'runway': norm_end(end) if end else None, 'instruction': kind}}


def build(icao, seed, split):
    pools = POOL[split]; A = ChartIdentifiers(icao); rng = random.Random(seed); g = Gen(rng, pools)
    cross = sorted((tw, rid) for tw, rids in A.crossings.items() for rid in rids if re.fullmatch(r'[A-Z]{1,2}\d?', tw))
    no_cross = sorted(t for t in A.vocab if t not in A.crossings and re.fullmatch(r'[A-Z]{1,2}\d?', t))
    hs_tw = {tw for v in A.hot_spots.values() for tw in v}
    hot = [c for c in cross if c[0] in hs_tw]
    inter = sorted(A.intersecting_runways())
    out = []
    for i, kind in enumerate(PLAN + PLAN_V4_EXTRA, 1):
        tw, rid = rng.choice(hot if kind.endswith('HOTSPOT') and hot else cross)
        end = rng.choice(runway_ends_of(A, rid))
        tw2 = rng.choice([t for t, r in cross if r == rid])                  # an intersection on the same runway
        (a_say, a), (b_say, b) = g.callsign(), g.callsign()
        L, note = [], None
        g.other_ends = [e for r in sorted({r for _, r in cross} - {rid}) for e in runway_ends_of(A, r)]
        pool = g.p(); P = CAT['pilot'][pool]   # one pool per scenario for the pilot side and the scenario-level forms

        def conv(t, cs_say, cs, kind_, end_, key, pkey, tw_=None, **kw):
            f = g.fill(cs_say, end_, tw_, **kw)
            return [tx(t, 'ATC', g.atc(key, **f), cs, kind_, end_), tx(t + 4, 'PILOT', P[pkey].format(**f), cs, kind_, end_)]

        if kind in ('C_LAND_CROSS', 'C_LAND_CROSS_HOTSPOT', 'C_HOLD_NO_READBACK', 'N_CROSS_OTHER_RWY', 'N_HOLD_SHORT', 'N_EXIT_TAXI_THEN_CROSS',
                    'N_CLEAR_REPORT_THEN_CROSS', 'N_GO_AROUND_THEN_CROSS'):
            L += conv(0, a_say, a, 'LANDING_CLEARANCE', end, 'land', 'land')
        if kind in ('C_TKOF_CROSS', 'C_TKOF_LAND_SAME'):
            L += conv(0, a_say, a, 'TAKEOFF_CLEARANCE', end, 'tkof', 'tkof', tw2=say_twy(tw2))
        if kind in ('C_LAND_CROSS', 'C_LAND_CROSS_HOTSPOT', 'C_TKOF_CROSS'):
            L += conv(18, b_say, b, 'CROSS', end, 'cross', 'cross', tw)
        if kind == 'C_LUAW_LAND':
            L += conv(0, a_say, a, 'LUAW', end, 'luaw', 'luaw', tw2=say_twy(tw2))
            L += conv(14, b_say, b, 'LANDING_CLEARANCE', end, 'land_traffic_luaw', 'land')
        if kind == 'C_TKOF_LAND_SAME':
            L += conv(14, b_say, b, 'LANDING_CLEARANCE', end, 'land', 'land')
        if kind == 'C_TKOF_LAND_INTERSECT':
            if inter:
                r1, r2 = rng.choice(inter); e1, e2 = rng.choice(runway_ends_of(A, r1)), rng.choice(runway_ends_of(A, r2))
                L += conv(0, a_say, a, 'TAKEOFF_CLEARANCE', e1, 'tkof', 'tkof', tw2=say_twy(rng.choice([t for t, r in cross if r == r1] or [tw2])))
                L += conv(14, b_say, b, 'LANDING_CLEARANCE', e2, 'land', 'land')
            else:   # no intersecting runways at this airport: the same-runway pair (R3)
                L += conv(0, a_say, a, 'TAKEOFF_CLEARANCE', end, 'tkof', 'tkof', tw2=say_twy(tw2))
                L += conv(14, b_say, b, 'LANDING_CLEARANCE', end, 'land', 'land')
                note = f'{icao} has no intersecting runways: R3 (same runway) used instead of R4'
        if kind == 'C_HOLD_NO_READBACK':   # 3-7-2h: 'Roger' is not a read back of hold short instructions
            f = g.fill(b_say, end, tw)
            L += [tx(18, 'ATC', next(t for k, t in CAT['atc']['hold'][pool] if k == 'HOLD_SHORT').format(**f), b, 'HOLD_SHORT', end)]
            L += [tx(22, 'PILOT', P['roger'].format(**f), b, 'OTHER', None)]
            L += [tx(40, 'ATC', g.atc('read_back_hold', **f), b, 'OTHER', None)]
            L += [tx(44, 'PILOT', P['hold'].format(**f), b, 'HOLD_SHORT', end)]
        if kind == 'N_CROSS_OTHER_RWY':
            other = [c for c in cross if c[1] != rid and tuple(sorted((c[1], rid))) not in A.intersecting_runways()] or [c for c in cross if c[1] != rid]
            tw3, rid3 = rng.choice(other)
            L += conv(18, b_say, b, 'CROSS', rng.choice(runway_ends_of(A, rid3)), 'cross', 'cross', tw3)
        if kind == 'N_HOLD_SHORT':
            k, t_ = rng.choice(CAT['atc']['hold'][pool])
            dep = rng.choice([e for r in sorted({r for _, r in cross} - {rid}) for e in runway_ends_of(A, r)] or [end])
            f = g.fill(b_say, end, tw, rw_dep=say_rwy(dep))
            L += [tx(18, 'ATC', t_.format(**f), b, k, end), tx(22, 'PILOT', P['hold'].format(**f), b, 'HOLD_SHORT', end)]
        if kind == 'N_EXIT_TAXI_THEN_CROSS':
            ex = rng.choice(no_cross); f = g.fill(a_say, None, ex)
            L += [tx(40, 'ATC', g.atc('exit', **f), a, 'TAXI', None), tx(48, 'ATC', g.atc('taxi', **f), a, 'TAXI', None)]
            L += conv(55, b_say, b, 'CROSS', end, 'cross', 'cross', tw)
        if kind == 'N_CLEAR_REPORT_THEN_CROSS':
            ex = rng.choice(no_cross); f = g.fill(a_say, end, ex)
            L += [tx(45, 'PILOT', CAT['pilot_report_clear'][pool].format(**f), a, 'CROSSING_COMPLETE', end)]
            L += conv(55, b_say, b, 'CROSS', end, 'cross', 'cross', tw)
        if kind == 'N_GO_AROUND_THEN_CROSS':
            f = g.fill(a_say, end)
            L += [tx(30, 'ATC', g.atc('go_around', **f), a, 'GO_AROUND', None), tx(33, 'PILOT', P['go_around'].format(**f), a, 'GO_AROUND', None)]
            L += conv(45, b_say, b, 'CROSS', end, 'cross', 'cross', tw)
        if kind in ('N_LUAW_THEN_TKOF', 'N_LUAW_CONTINUE'):
            L += conv(0, a_say, a, 'LUAW', end, 'luaw', 'luaw', tw2=say_twy(tw2))
            if kind == 'N_LUAW_CONTINUE':
                L += conv(14, b_say, b, 'OTHER', end, 'continue_traffic_luaw', 'continue')
                L[-1] = (L[-1][0], {'fields': dict(L[-1][1]['fields'], runway=None)})   # the pilot's 'continue' names no runway
            L += conv(30 if kind == 'N_LUAW_CONTINUE' else 40, a_say, a, 'TAKEOFF_CLEARANCE', end, 'tkof', 'tkof', tw2=say_twy(tw2))
        if kind == 'RB_WRONG_RUNWAY':   # 2-4-3a: the read back states another runway
            wrong = rng.choice(g.other_ends)
            f = g.fill(a_say, end); fw = dict(f, rw=say_rwy(wrong))
            L += [tx(0, 'ATC', g.atc('land', **f), a, 'LANDING_CLEARANCE', end), tx(4, 'PILOT', P['land'].format(**fw), a, 'LANDING_CLEARANCE', wrong)]
        if kind == 'RB_MISSING_RUNWAY':   # 2-4-3 NOTE 1c: the runway is not read back
            f = g.fill(a_say, end)
            L += [tx(0, 'ATC', g.atc('land', **f), a, 'LANDING_CLEARANCE', end), tx(4, 'PILOT', CAT['pilot_missing_runway'][pool].format(**f), a, 'LANDING_CLEARANCE', None)]
        sid = f'SYN_{icao}_{split.upper()}_{i:02d}_{kind}'
        rule = RULE.get(kind)
        if kind == 'C_TKOF_LAND_INTERSECT' and note:
            rule = 'R3'
        ref = {a: 'a', b: 'b'}   # which aircraft speaks (voice choice in render.py); not truth
        on_freq = sorted({a, b} if kind not in PLAN_V4_EXTRA else {a})
        others = [g.callsign()[1] for _ in range(8)]                                            # other traffic on frequency
        perfect = sorted(set(on_freq + others))
        realistic = sorted({c for c in perfect if rng.random() >= 0.15} | {g.callsign()[1] for _ in range(3)})   # non-ADS-B dropped; 3 not on frequency
        txs = [dict(x[0], id=f'{sid}_{k:02d}', speaker_ref='atc' if x[0]['speaker'] == 'ATC' else ref[x[1]['fields']['callsigns'][0]]) for k, x in enumerate(L)]
        out.append(({'id': sid, 'airport': icao, 'SIMULATED': True, 'split': split, 'phrasing_pool': pool if len(pools) == 1 else f'{pool} (pilot/scenario forms; ATC elements drawn from {"/".join(pools)})', 'template': kind, 'basis': CAT['scenarios'][kind],
                     'generator': 'eval/synthetic/generate_v4.py (phrasings: phraseology_7110.json, FAA JO 7110.65BB)', 'seed': seed,
                     'context': {'runway': rid.split(':')[-1], 'taxiway': tw, 'hot_spot': next((h for h, v in A.hot_spots.items() if tw in v), None)},
                     'note': (note + '. ' if note else '') + 'Synthetic phraseology from chart identifiers; no positions; no predictions on real geometry. Evaluation harness only.',
                     'transmissions': txs},
                    {'id': sid, 'EVALUATION_ONLY': True, 'expected_alert': kind.startswith('C_'), 'expected_rules': [rule] if rule else [], 'conflict_t': None,
                     'expected_readback_mismatch': kind.startswith('RB_'),
                     'section8_case': kind in ('N_EXIT_TAXI_THEN_CROSS', 'N_CLEAR_REPORT_THEN_CROSS'),
                     'transmissions': [dict(x[1], id=f'{sid}_{k:02d}') for k, x in enumerate(L)]},
                    {'id': sid, 'SIMULATED': True, 'note': 'Callsigns on frequency (stand-in for ADS-B / flight plans); an observation, not truth labels.',
                     'perfect': perfect, 'realistic': realistic}))
    d = ROOT / 'eval/synthetic' / split / icao.lower(); d.mkdir(parents=True, exist_ok=True)
    for sc, tr, tf in out:
        (d / f"{sc['id']}.scenario.json").write_text(json.dumps(sc, indent=1) + '\n'); (d / f"{sc['id']}.truth.json").write_text(json.dumps(tr, indent=1) + '\n')
        (d / f"{sc['id']}.traffic.json").write_text(json.dumps(tf, indent=1) + '\n')
    print(f'{icao} {split}: {len(out)} scenarios, {sum(len(s["transmissions"]) for s, _, _ in out)} transmissions -> {d.relative_to(ROOT)} '
          f'({sum(1 for _, t, _ in out if t["expected_alert"])} expected alerts, {sum(1 for _, t, _ in out if t["expected_readback_mismatch"])} readback mismatches; pools {pools}; seed {seed})')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); ap.add_argument('--split', choices=tuple(POOL), required=True)
    ap.add_argument('--seed', type=int, required=True)
    a = ap.parse_args()
    build(a.icao.upper(), a.seed, a.split)
