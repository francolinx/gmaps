"""N6 NO-LOSS CHECK: reservation expiry + warning dedup + open-warning cap must not lose a true alert.

  python3 eval/expiry_check.py            # deterministic sets (no model)
  python3 eval/expiry_check.py --llm      # also held-out4 as deployed (Whisper prompt + word confidence, realistic traffic, LLM fallback)

Every scenario is run twice through the harness's deployed-path emulation (eval/harness.py run_scenario), on the same inputs:
  legacy   = the ledger as before N6 (no expiry, no dedup, no cap);
  runtime  = the ledger as configured in data/specs/runtime_config.json (soft expiry + hard limit, per-runway R17, conflict auto-close,
           bounded history, max_open_warnings, dedup).
With --llm the model's answer for each line is cached on the first run and replayed on the second, so both see identical parses.
Pass conditions, per scenario: same scenario-level outcome (TP/FP/FN/TN) and same first-alert time; every legacy alert is still
raised (same rule, severity, time and the same two authorizations; a missing one must be an exact duplicate of a kept one);
no reservation expires inside a conflict window (at or before the scenario's reference conflict time or its last alert); the
cap is never reached. Sets: SIM-1 episodes (incl. E1), synthetic dev/heldout, dev3/heldout3, dev4/heldout4 (SIMULATED).
Every scenario in these sets is <= 78 s long and the shortest soft expiry is 180 s, so expiry cannot fire inside them: the
no-loss result holds, but the sets do not exercise expiry. Expiry itself is exercised by eval/expiry_gaps.py (N7: gaps of 2-25 min,
alert / advisory / nothing per gap), whose table is included here.
Writes eval/results/expiry_check_2026-10-10.json (summary; evaluation only).
"""
import argparse, functools, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'eval'), str(ROOT / 'lib')]
import harness as H                          # noqa: E402
from gmaps_core.ledger import Ledger         # noqa: E402

CFG = json.loads((ROOT / 'data/specs/runtime_config.json').read_text())
RT = {'hints': 'classifier', 'clock': True, 'timeout': CFG['readback_timeout_s'], 'rb_conf': CFG['readback_mismatch_min_word_conf'],
      'expiry': CFG['reservation_expiry_s'], 'hard_limit': CFG['reservation_hard_limit_s']}
CONFIGS = {   # name: (runtime options, text, splits)
    'labels_asr': ({'expiry': CFG['reservation_expiry_s'], 'hard_limit': CFG['reservation_hard_limit_s']}, 'asr', ('dev', 'heldout', 'dev3', 'heldout3', 'dev4', 'heldout4')),
    'runtime_asr': (RT, 'asr', ('dev', 'heldout', 'dev3', 'heldout3', 'dev4', 'heldout4')),
    'runtime_script': (RT, 'script', ('dev', 'heldout', 'dev3', 'heldout3', 'dev4', 'heldout4')),
    'runtime_words': (RT, 'asr_prompt_words', ('dev4', 'heldout4')),
}


def key(w):
    a, b = w.get('authorization_a') or {}, w.get('authorization_b') or {}
    return (w['rule_id'], w['severity'], w['t_fired'], a.get('callsign'), a.get('type'), a.get('t_issued'), b.get('callsign'), b.get('type'), b.get('t_issued'))   # event ids are a global counter


class CachedLLM:
    def __init__(self, inner):
        self.inner, self.cache = inner, {}

    def parse(self, text, traffic=None):
        k = (text, json.dumps(traffic, sort_keys=True))
        if k not in self.cache:
            self.cache[k] = self.inner.parse(text, traffic)
        f, info = self.cache[k]
        return (json.loads(json.dumps(f)) if f is not None else None), dict(info)


def run(builder, mode, ledgers):
    def make(*a, **kw):
        if mode == 'legacy':
            kw.update(expiry=None, dedup=False, max_open_warnings=None, hard_limit_s=None, history_keep=None, auto_close_s=None)
        else:
            kw.setdefault('max_open_warnings', CFG['max_open_warnings'])
        led = Ledger(*a, **kw); ledgers.append(led); return led
    H.Ledger = make
    try:
        return builder()
    finally:
        H.Ledger = Ledger


def compare(name, builder):
    leg_l, new_l = [], []
    leg, new = run(builder, 'legacy', leg_l), run(builder, 'runtime', new_l)
    out = {'scenarios': len(leg), 'outcome_changed': [], 'first_alert_changed': [], 'lost_alerts': [], 'deduplicated': 0,
           'expiries': 0, 'expiries_in_conflict_window': [], 'cap_events': sum(len(l.cap_events) for l in new_l), 'tp': 0, 'fn': 0, 'fp': 0}
    for s0, s1, led in zip(leg, new, new_l):
        a0, a1 = H.alerts_of(s0['warnings']), H.alerts_of(s1['warnings'])
        exp = s0.get('expected_alert')
        o0, o1 = bool(a0), bool(a1)
        if exp is not None:
            out['tp'] += exp and o1; out['fn'] += exp and not o1; out['fp'] += (not exp) and o1
        if o0 != o1:
            out['outcome_changed'].append(s0['scenario'])
        if o0 and o1 and min(w['t_fired'] for w in a0) != min(w['t_fired'] for w in a1):
            out['first_alert_changed'].append(s0['scenario'])
        k1 = {key(w) for w in a1}
        # deduplicated = the same conflict (rule, severity, the two callsign/type) raised again while its warning was open:
        # the runtime warning was updated in place at that time (t_repeats), never a new or a later alert
        dedup_keys = {(k[0], k[1], k[3], k[4], k[6], k[7], t) for w in a1 for k in [key(w)] for t in [w['t_fired']] + (w.get('t_repeats') or [])}
        for w in a0:
            k = key(w)
            if k not in k1:
                if (k[0], k[1], k[3], k[4], k[6], k[7], k[2]) in dedup_keys:
                    out['deduplicated'] += 1
                else:
                    out['lost_alerts'].append([s0['scenario'], list(k)])
        out['expiries'] += len(led.expiries)
        window_end = max([s0.get('reference_t') or 0] + [w['t_fired'] for w in a0])
        for e in led.expiries:
            if exp and e['t'] <= window_end:
                out['expiries_in_conflict_window'].append([s0['scenario'], e['callsign'], e['type'], e['t']])
    out['pass'] = not (out['outcome_changed'] or out['first_alert_changed'] or out['lost_alerts'] or out['expiries_in_conflict_window'] or out['cap_events'])
    return out


def longest():
    d = [max(x['t'] for x in json.loads(p.read_text())['transmissions']) - min(x['t'] for x in json.loads(p.read_text())['transmissions'])
         for p in (ROOT / 'eval/synthetic').glob('*/*/*.scenario.json')]
    d += [max(x['t'] for x in json.loads(p.read_text())['events']) - min(x['t'] for x in json.loads(p.read_text())['events']) for p in (ROOT / 'episodes').glob('SIM_*.transcript.json')]
    return {'n': len(d), 'max_s': max(d), 'shortest_expiry_s': min(CFG['reservation_expiry_s'].values())}


def limits():
    """N7: the targeted expiry cases (eval/expiry_gaps.py): what the second authorization raises at each gap."""
    import expiry_gaps as G
    return {name: {f'{g} min': G.run_case(f, sec, g * 60)['raised'] for g in G.GAPS_MIN} for name, (f, sec) in G.CASES.items()}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--llm', action='store_true'); a = ap.parse_args()
    report = {'note': 'N6/N7 no-loss check: legacy ledger vs runtime ledger (soft expiry + hard limit + dedup + cap) on identical inputs. SIMULATED sets only.',
              'config': {k: CFG[k] for k in ('reservation_expiry_s', 'reservation_hard_limit_s', 'max_open_warnings', 'readback_timeout_s')}, 'sets': {}}
    H.RT.clear()
    report['sets']['sim (SIM-1 episodes incl. E1, labels)'] = compare('sim', lambda: H.set_sim(H.asr_index()))
    for cname, (rt, text, splits) in CONFIGS.items():
        for split in splits:
            def b(split=split, rt=rt, text=text):
                H.RT.clear(); H.RT.update(rt); H.USE_SCRIPT = text == 'script'; H.ASR_SUFFIX = 'asr' if text == 'script' else text
                return H.set_synthetic(split)
            report['sets'][f'{cname}/{split}'] = compare(f'{cname}/{split}', b)
    if a.llm:
        c = CFG['llm']; H.LLM = {}
        from llm_fallback import LLMFallback
        cache = {}
        def mk(*x, **kw):
            k = json.dumps([x, kw], sort_keys=True, default=str)
            if k not in cache:
                cache[k] = CachedLLM(LLMFallback(*x, **kw))
            return cache[k]
        H.LLMFallback = mk
        def b():
            H.RT.clear(); H.RT.update(RT, traffic='realistic', llm=True, llm_cfg={'base_url': c['base_url'], 'model': c['model'], 'max_tokens': c['max_tokens'], 'timeout': c['timeout_s']})
            H.USE_SCRIPT = False; H.ASR_SUFFIX = 'asr_prompt_words'
            return H.set_synthetic('heldout4')
        report['sets']['deployed_llm/heldout4 (prompt+words, realistic traffic, LLM fallback)'] = compare('llm', b)
    H.RT.clear()
    report['longest_scenario_s'] = longest()
    report['expiry_gaps (eval/expiry_gaps.py; SIM-1 map, fictional)'] = limits()
    tot = {k: sum(v[k] if isinstance(v[k], int) else len(v[k]) for v in report['sets'].values())
           for k in ('scenarios', 'outcome_changed', 'first_alert_changed', 'lost_alerts', 'deduplicated', 'expiries', 'expiries_in_conflict_window', 'cap_events', 'tp', 'fn', 'fp')}
    report['total'] = dict(tot, all_pass=all(v['pass'] for v in report['sets'].values()))
    (ROOT / 'eval/results/expiry_check_2026-10-10.json').write_text(json.dumps(report, indent=1) + '\n')
    for k, v in report['sets'].items():
        print(f"{'PASS' if v['pass'] else 'FAIL'} {k}: {v['scenarios']} scenarios, tp {v['tp']} fn {v['fn']} fp {v['fp']}, lost {len(v['lost_alerts'])}, "
              f"dedup {v['deduplicated']}, expiries {v['expiries']} (in window {len(v['expiries_in_conflict_window'])}), cap {v['cap_events']}")
    print('TOTAL', json.dumps(report['total']))
    sys.exit(0 if report['total']['all_pass'] else 1)


if __name__ == '__main__':
    main()
