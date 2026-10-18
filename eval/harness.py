"""EVALUATION HARNESS. One command: run a scenario set, report accuracy, alerts, lead time and latency.

  python3 eval/harness.py                      # all sets
  python3 eval/harness.py --set sim            # fictional SIM-1 episodes (eval/labels)
  python3 eval/harness.py --set klga_real      # private clips confirmed LaGuardia (real_clip_sets.json), TEXT ONLY; scored where Franco labelled
  python3 eval/harness.py --set khwd_real      # private clips confirmed Hayward; same rules (unknown_airport clips are never scored)
  python3 eval/harness.py --set synthetic      # generated scenarios (eval/synthetic/*/), results marked SIMULATED

Evaluation code: it may read eval/truth, eval/labels and scenario truth; the runtime/predictor never does (tests/run_all.py).
Per transmission: the hypothesis is the library parser (the same code the agent's parse_transmission tool calls) on the
input text (Whisper text where an ASR result exists, else the script, marked). Reference fields:
  sim        parse of the clean script line (reference_kind 'script_parse': measures what ASR does to the fields)
  klga_real  the fixture line Franco verified for that clip (labels file); unlabelled / wrong / partial -> UNSCORED
  synthetic  the generator's structured truth (reference_kind 'scenario_truth')
Alerts: a scenario-level alert is a new ledger warning of severity CONFLICT or CAUTION. Precision/recall over scenarios;
lead time = reference conflict time - first alert time (positive = early). Latency: library path per transmission (ms),
Whisper seconds where measured, and the full-stack agent figure from eval/results/latency_e1.json when present.
Real-airport geometry: no predictions are run or shown on real airports; synthetic results are SIMULATED and stay here.
"""
import argparse, json, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.parser import parse          # noqa: E402
from gmaps_core.ledger import Ledger         # noqa: E402
from gmaps_core.parser import classify_speaker   # noqa: E402
from gmaps_core.traffic import match_callsigns   # noqa: E402
from gmaps_core.asr_conf import runway_confidence   # noqa: E402
sys.path.insert(0, str(ROOT / 'venue'))
from llm_fallback import LLMFallback, to_event, identify   # noqa: E402

VENUE = Path.home() / 'gmaps_venue'
RESULTS = ROOT / 'eval/results'
ALERT_SEV = {'CONFLICT', 'CAUTION'}
USE_SCRIPT = False
HINTS = True
ASR_SUFFIX = 'asr'
RT = {}          # round 3 runtime emulation options (see run_scenario)
LLM = None


def fields(ev):
    hold = next((h.get('on_runway') or h.get('runway') for h in ev.get('hold_constraints') or [] if h.get('on_runway') or h.get('runway')), None)
    # labelling rule (eval/synthetic/phraseology_7110.json, fixed before scoring): a taxi or hold instruction is about the runway it holds short of
    rwy = (hold if ev.get('kind') in ('TAXI', 'HOLD_SHORT') and hold else None) or ev.get('runway') or hold
    return {'callsigns': sorted(ev.get('actor_candidates') or []), 'runway': canon_rwy(rwy), 'instruction': ev.get('kind') or None}


def canon_rwy(r):   # '09R' and '9R' name the same runway end
    return (str(r).lstrip('0') or '0') if r else None


def asr_index():
    """clip name -> (Whisper text, decode seconds) from every demo run's asr_results.json (latest wins)."""
    idx = {}
    for p in sorted(VENUE.glob('log/asr_*/asr_results.json'), key=lambda p: p.stat().st_mtime):
        for c in json.loads(p.read_text()).get('clips', []):
            name = c.get('clip') or c.get('file')
            if name and c.get('asr_text_raw') is not None:
                idx[name] = (c['asr_text_raw'], c.get('asr_s'))
    return idx


def score_fields(rows):
    sc = [r for r in rows if r.get('reference') is not None]
    has = lambda r, k: k in r['reference'] and ('reference_fields' not in r or k in r['reference_fields'])   # real clips: only the labelled fields
    def acc(k):
        x = [r for r in sc if has(r, k)]
        return round(sum(r['hypothesis'][k] == r['reference'][k] for r in x) / len(x), 3) if x else None
    return {'scored': len(sc), 'unscored': len(rows) - len(sc), 'callsign_acc': acc('callsigns'), 'runway_acc': acc('runway'), 'instruction_acc': acc('instruction')}


def run_scenario(lines, airport, resolver=None, hints=True, rt=None):
    """lines: [{'t', 'text', 'speaker', 'reference' (fields or None), 'asr_s'}]. Returns per-line rows and ledger warnings.
    rt (round 3, runtime emulation): {'hints': 'labels'|'none'|'classifier', 'traffic': [...]|None, 'llm': LLMFallback|None,
    'timeout': s, 'clock': True}. As the deployed system: speaker role from the classifier, callsigns matched against the traffic
    list, lines the grammar cannot type go to the LLM (validated; dropped lines counted) and otherwise to the agent (same grammar),
    and the ledger is ticked every second (the fast path's clock)."""
    rt = rt or {}
    led = Ledger(resolver, timers={'R7': rt['timeout']} if rt.get('timeout') else None, readback_conf_min=rt.get('rb_conf'), **({'expiry': rt['expiry']} if 'expiry' in rt else {}),
                 **({'hard_limit_s': rt['hard_limit']} if 'hard_limit' in rt else {}))
    rows, llm_events, llm_log, repaired_events = [], set(), [], set()
    order = sorted(lines, key=lambda x: x['t'])
    clock = rt.get('clock')
    last_t = None
    for ln in order:
        if clock and last_t is not None:
            for k in range(int(last_t) + 1, int(ln['t']) + 1):
                led.tick(k)
        t0 = time.perf_counter()
        mode = rt.get('hints', 'labels' if hints else 'none')
        hint = ln.get('speaker') if mode == 'labels' else classify_speaker(ln['text'])[0] if mode == 'classifier' else None
        ev = parse(ln['text'], airport, speaker_hint=hint, resolver=resolver, event_time=ln['t'])
        if rt.get('traffic'):
            match_callsigns(ev, rt['traffic'])
        grammar = ev.get('parse_path') == 'grammar' and ev['kind'] not in ('OTHER', 'UNKNOWN', None) and bool(ev.get('actor_candidates'))
        llm_info = None
        if not grammar and rt.get('llm') is not None:
            f, llm_info = rt['llm'].parse(ln['text'], rt.get('traffic'))
            llm_info = dict(llm_info, dropped=f is None, actionable=bool(f and f.get('actionable')), t=ln['t'], text=ln['text'])
            llm_log.append(llm_info)
            if llm_info['actionable']:
                cs_ok, why = identify(f['callsign'], ln['text'], set(rt.get('traffic') or []) | set(led.actors))
                if cs_ok is None:
                    llm_info.update(actionable=False, unidentified=why)
                else:
                    f['callsign'] = cs_ok; llm_info['callsign_repaired'] = why
            if llm_info['actionable']:
                if hint in ('ATC', 'PILOT'):
                    f['speaker_role'] = hint
                ev = to_event(f, ln['text'], airport, ev['event_id'], ln['t'], None, resolver)
                if rt.get('traffic'):
                    match_callsigns(ev, rt['traffic'])
                if llm_info.get('callsign_repaired'):
                    ev['callsign_repaired'] = True
                llm_events.add(ev['event_id'])
        rw = ev.get('runway') or next((h.get('runway') for h in ev.get('hold_constraints') or [] if h.get('runway')), None)
        if rw and ln.get('words'):
            ev['runway_confidence'] = runway_confidence(ln['words'], rw)
        elif rw and ln.get('input') == 'script':
            ev['runway_confidence'] = 1.0   # clean script text: the words are certain
        if ev.get('callsign_repaired'):
            repaired_events.add(ev['event_id'])
        led.ingest(ev, ln['t']); led.tick(ln['t'])
        ms = (time.perf_counter() - t0) * 1000
        rows.append({'t': ln['t'], 'input': ln.get('input', 'script'), 'text': ln['text'], 'hypothesis': fields(ev), 'reference': ln.get('reference'),
                     'library_ms': round(ms, 2), 'asr_s': ln.get('asr_s'), 'speaker_label': ln.get('speaker'), 'speaker_hint_used': hint,
                     'speaker_resolved': ev.get('speech_act_resolved', ev['speech_act']), 'parse_path': ev.get('parse_path'), 'llm': llm_info,
                     'callsign_match': ev.get('callsign_match')})
        last_t = ln['t']
    if clock and last_t is not None:
        for k in range(int(last_t) + 1, int(last_t) + int(led.T['R7']) + 3):
            led.tick(k)
    for w in led.warnings:
        ids = {(w.get('authorization_a') or {}).get('event_id'), (w.get('authorization_b') or {}).get('event_id')}
        w['llm_parse'] = bool(ids & llm_events)
        w['callsign_repaired'] = bool(ids & repaired_events)
    return rows, led.warnings


def alerts_of(warnings, include_llm=True, include_repaired=True):
    """Conflict alerts as scored since round 1: CONFLICT/CAUTION warnings of category conflict or missed_readback.
    Readback mismatches (R14) are their own category, scored separately."""
    return [w for w in warnings if w.get('severity') in ALERT_SEV and w.get('category', 'conflict') != 'readback_mismatch'
            and (include_llm or not w.get('llm_parse')) and (include_repaired or not w.get('callsign_repaired'))]


def mismatch_alerts(warnings):
    return [w for w in warnings if w.get('category') == 'readback_mismatch' and w.get('severity') in ALERT_SEV]


def set_sim(asr):
    from gmaps_core.sim.simmap import SimAirport
    out = []
    for lab_p in sorted((ROOT / 'eval/labels').glob('SIM_*.labels.json')):
        eid = lab_p.name.split('.')[0]; tp = ROOT / f'episodes/{eid}.transcript.json'
        if not tp.exists():
            continue
        lab = json.loads(lab_p.read_text()); tr = json.loads(tp.read_text()); ap = SimAirport(); lines = []
        for e in tr['events']:
            clip = f"{eid}_{int(e['t'])}.wav"; hint = 'ATC' if e['speaker'] == 'ATC' else 'PILOT'
            ref = fields(parse(e['text_clean'], 'XSIM', speaker_hint=hint, resolver=ap, event_time=e['t']))
            text, asr_s, inp = (asr[clip][0], asr[clip][1], 'whisper') if clip in asr else (e['text_clean'], None, 'script')
            lines.append({'t': e['t'], 'text': text, 'speaker': hint, 'reference': ref, 'asr_s': asr_s, 'input': inp})
        rows, warns = run_scenario(lines, 'XSIM', SimAirport())
        ref_t = (lab.get('truth_encounter_interval') or [None])[0] or lab.get('vehicle_passes_hold_line_at')
        out.append({'scenario': eid, 'kind': 'SIMULATED (fictional SIM-1)', 'reference_kind': 'script_parse', 'expected_alert': bool(lab['expected_ledger_rules']),
                     'expected_rules': lab['expected_ledger_rules'], 'reference_t': ref_t, 'rows': rows, 'warnings': warns})
    return out


def set_real(name='klga_real'):
    """Real private clips, TEXT ONLY (committed Whisper text), scored only where Franco labelled them:
    klga_real / khwd_real = clips with per-clip airport evidence (venue/real_clip_sets.py -> ~/gmaps_venue/labels/real_clip_sets.json);
    unknown_airport clips are never scored. Hypothesis = the deployed path (speaker classifier, chart identifiers).
    Reference per field: the label's ref_* fields when given; else, for a 'verified' klga clip, its matched LGA fixture line."""
    sys.path.insert(0, str(ROOT / 'eval/synthetic'))
    from chart_ids import ChartIdentifiers
    clips = json.loads((ROOT / 'venue_traces/real_asr_text/asr_real_text_only.json').read_text())['clips']
    sets_p, lab_p, map_p = VENUE / 'labels/real_clip_sets.json', VENUE / 'labels/klga_real_clip_labels.json', VENUE / 'labels/klga_clip_mapping.json'
    sets = json.loads(sets_p.read_text())['clips'] if sets_p.exists() else {}
    labels = json.loads(lab_p.read_text()).get('labels', {}) if lab_p.exists() else {}
    mapping = {c['clip']: c for c in json.loads(map_p.read_text())['clips']} if map_p.exists() else {}
    fxp = ROOT / 'ui_scaffold/samples/LGA_2026-03-22.json'
    fx = json.loads(fxp.read_text())['timeline'] if fxp.exists() else []
    icao = {'klga_real': 'KLGA', 'khwd_real': 'KHWD'}[name]
    A = ChartIdentifiers(icao)
    mine = [c for c in clips if (sets.get(c['clip']) or {}).get('set') == name] if sets else ([c for c in clips] if name == 'klga_real' else [])
    order = sorted(mine, key=lambda c: (mapping.get(c['clip'], {}).get('line_t') if name == 'klga_real' and mapping.get(c['clip'], {}).get('line_t') is not None else 1e9, c['clip']))
    led, rows = Ledger(A), []
    for k, c in enumerate(order):
        lab = labels.get(c['clip'], {}); m = mapping.get(c['clip'], {})
        ref, given = {}, []
        if lab.get('judgement') == 'verified' and name == 'klga_real' and m.get('line_index') is not None and fx:
            ref = fields(fx[m['line_index']].get('event') or {}); given = ['callsigns', 'runway', 'instruction']
        if lab.get('ref_callsign'):
            ref['callsigns'] = [lab['ref_callsign'].upper()]; given = sorted(set(given) | {'callsigns'})
        if lab.get('ref_runway'):
            ref['runway'] = canon_rwy(lab['ref_runway'].upper()); given = sorted(set(given) | {'runway'})
        if lab.get('ref_instruction'):
            ref['instruction'] = lab['ref_instruction']; given = sorted(set(given) | {'instruction'})
        role = classify_speaker(c['asr_text_raw'])[0]
        ev = parse(c['asr_text_raw'], icao, speaker_hint=role, resolver=A, event_time=k * 10)
        before = {w['warning_id'] for w in led.warnings}
        led.ingest(ev, k * 10)
        r14 = [w for w in led.warnings if w['warning_id'] not in before and w.get('category') == 'readback_mismatch']
        rows.append({'t': k * 10, 'clip': c['clip'], 'input': 'whisper (text only, committed)', 'text': c['asr_text_raw'], 'hypothesis': fields(ev),
                     'reference': ref or None, 'reference_fields': given, 'label': lab.get('judgement') or 'UNLABELLED', 'library_ms': None, 'asr_s': c.get('asr_s'),
                     'speaker_label': lab.get('ref_speaker') or None, 'speaker_hint_used': role, 'ref_readback': lab.get('ref_readback'),
                     'readback_mismatch_candidate': bool(r14), 'speaker_resolved': ev.get('speech_act_resolved', ev['speech_act'])})
    return [{'scenario': f'{icao}_real_clips', 'kind': f'REAL ATC audio ({icao}), local text only (labels by Franco)', 'reference_kind': 'franco_labels',
             'expected_alert': None, 'rows': rows, 'warnings': [], 'airport_set': name, 'set_size': len(mine),
             'note': 'Field accuracy where labelled; unknown_airport clips are never scored. No conflict alerts are scored on real audio.'}]


def set_klga_real():
    return set_real('klga_real')


def _wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); h = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5
    return round((c - h) / d, 3), round((c + h) / d, 3)


def labels_needed(name, scs):
    """How many labels each metric needs to be meaningful. Accuracy: +/-0.10 at 95 % (normal approximation,
    observed p, or 0.5 below 10 labels). Readback-mismatch precision (shadow-mode gate >= 0.8): labelled candidates needed for
    the 95 % Wilson lower bound to reach 0.8 at the observed precision (all-correct if none yet)."""
    rows = [r for s in scs for r in s['rows']]
    out = {'set': name, 'clips_in_set': sum(s.get('set_size', len(s['rows'])) for s in scs), 'metrics': {}}
    for f in ('callsigns', 'runway', 'instruction'):
        sc = [r for r in rows if r.get('reference') and f in (r.get('reference_fields') or [])]
        k = sum(r['hypothesis'][f] == r['reference'].get(f) for r in sc); n = len(sc)
        p = k / n if n >= 10 else 0.5
        need = int(__import__('math').ceil(1.96 ** 2 * p * (1 - p) / 0.1 ** 2)) or 10
        out['metrics'][f'{f}_accuracy'] = {'labelled': n, 'value': round(k / n, 3) if n else None, 'ci95': _wilson(k, n), 'needed_for_+-0.10': need,
                                           'more_needed': max(0, need - n)}
    sp = [r for r in rows if r.get('speaker_label') in ('ATC', 'PILOT')]
    k = sum(r['speaker_hint_used'] == r['speaker_label'] for r in sp); n = len(sp)
    p = k / n if n >= 10 else 0.5
    need = int(__import__('math').ceil(1.96 ** 2 * p * (1 - p) / 0.1 ** 2)) or 10
    out['metrics']['speaker_classifier_accuracy'] = {'labelled': n, 'value': round(k / n, 3) if n else None, 'ci95': _wilson(k, n), 'needed_for_+-0.10': need, 'more_needed': max(0, need - n)}
    cand = [r for r in rows if r.get('readback_mismatch_candidate') and r.get('ref_readback')]
    k = sum(r['ref_readback'] == 'wrong_runway' for r in cand); n = len(cand)
    p = k / n if n else 1.0
    need = next((m for m in range(1, 2000) if (_wilson(round(p * m), m) or (0, 0))[0] >= 0.8), None) if p > 0.8 else None
    out['metrics']['readback_mismatch_precision (shadow gate >= 0.8)'] = {
        'labelled_candidates': n, 'value': round(k / n, 3) if n else None, 'ci95': _wilson(k, n), 'candidates_now': sum(1 for r in rows if r.get('readback_mismatch_candidate')),
        'needed_labelled_candidates': need, 'more_needed': max(0, need - n) if need else 'not reachable at the observed precision (<= 0.8)'}
    return out


def set_synthetic(split='dev'):
    sys.path.insert(0, str(ROOT / 'eval/synthetic'))
    from chart_ids import ChartIdentifiers
    out, ids = [], {}
    for sp in sorted((ROOT / 'eval/synthetic' / split).glob('*/*.scenario.json')):
        sc = json.loads(sp.read_text()); truth = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
        asr_p = sp.with_name(sp.name.replace('.scenario.json', f'.{ASR_SUFFIX}.json'))
        asr = json.loads(asr_p.read_text()) if asr_p.exists() and not USE_SCRIPT else {}
        lines = []
        for tx, tt in zip(sc['transmissions'], truth['transmissions']):
            a = asr.get(tx['id'])
            lines.append({'t': tx['t'], 'text': a['text'] if a else tx['text'], 'speaker': tx['speaker'], 'asr_s': a and a.get('asr_s'), 'words': a and a.get('words'),
                          'input': 'whisper (synthetic TTS + radio noise)' if a else 'script', 'reference': dict(tt['fields'], runway=canon_rwy(tt['fields']['runway']))})
        A = ids.setdefault(sc['airport'], ChartIdentifiers(sc['airport']))
        rt = dict(RT)
        if RT.get('traffic'):
            tp = sp.with_name(sp.name.replace('.scenario.json', '.traffic.json'))
            rt['traffic'] = json.loads(tp.read_text())[RT['traffic']] if tp.exists() else None
        if RT.get('llm'):
            key = sc['airport']
            if key not in LLM:
                LLM[key] = LLMFallback(sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab), **RT['llm_cfg'])
            rt['llm'] = LLM[key]
        rows, warns = run_scenario(lines, sc['airport'], A, hints=HINTS, rt=rt)
        out.append({'scenario': sc['id'], 'kind': f"SIMULATED (synthetic {split}, {sc['airport']} identifiers; no predictions on real geometry)", 'reference_kind': 'scenario_truth',
                    'expected_alert': truth['expected_alert'], 'expected_rules': truth.get('expected_rules', []), 'reference_t': truth.get('conflict_t'),
                    'expected_readback_mismatch': truth.get('expected_readback_mismatch', False), 'template': sc.get('template'),
                    'expected_rb_candidate': sc.get('template') == 'RB_WRONG_RUNWAY', 'expected_incomplete': sc.get('template') == 'RB_MISSING_RUNWAY', 'rows': rows, 'warnings': warns})
    return out


def summarize(name, scs):
    rows = [r for s in scs for r in s['rows']]
    fs = score_fields(rows)
    exp = [s for s in scs if s.get('expected_alert') is not None]
    tp = sum(1 for s in exp if s['expected_alert'] and alerts_of(s['warnings'])); fp = sum(1 for s in exp if not s['expected_alert'] and alerts_of(s['warnings']))
    fn = sum(1 for s in exp if s['expected_alert'] and not alerts_of(s['warnings']))
    leads = []
    for s in exp:
        al = alerts_of(s['warnings'])
        if s['expected_alert'] and al and s.get('reference_t') is not None:
            leads.append(round(s['reference_t'] - min(w['t_fired'] for w in al), 2))
    lib = [r['library_ms'] for r in rows if r.get('library_ms') is not None]; asr_s = [r['asr_s'] for r in rows if r.get('asr_s') is not None]
    agent = json.loads((RESULTS / 'latency_e1.json').read_text()) if (RESULTS / 'latency_e1.json').exists() else None
    def pr(pred, key='expected_alert'):
        e = [x for x in scs if x.get(key) is not None]
        tp_ = sum(1 for x in e if x[key] and pred(x)); fp_ = sum(1 for x in e if not x[key] and pred(x)); fn_ = sum(1 for x in e if x[key] and not pred(x))
        return {'tp': tp_, 'fp': fp_, 'fn': fn_, 'precision': round(tp_ / (tp_ + fp_), 3) if tp_ + fp_ else None, 'recall': round(tp_ / (tp_ + fn_), 3) if tp_ + fn_ else None}
    cat = lambda x, c: [w for w in x['warnings'] if w.get('category', 'conflict') == c and w.get('severity') in ALERT_SEV]
    llm = [r['llm'] for r in rows if r.get('llm')]
    spk = [r for r in rows if r.get('speaker_label') in ('ATC', 'PILOT')]
    resolved_role = lambda r: 'PILOT' if r['speaker_resolved'] in ('readback', 'report', 'request') else 'ATC' if r['speaker_resolved'] == 'instruction' else None
    by_category = {
        'conflict_alerts (as in rounds 1-2: conflict + missed_readback)': pr(lambda x: bool(alerts_of(x['warnings']))),
        'conflict_alerts without llm_parse alerts': pr(lambda x: bool(alerts_of(x['warnings'], include_llm=False))),
        'conflict_alerts without callsign_repaired alerts': pr(lambda x: bool(alerts_of(x['warnings'], include_repaired=False))),
        'callsign_repaired_alerts': {'scenarios_with_one': sum(1 for x in scs if any(w.get('callsign_repaired') for w in alerts_of(x['warnings']))),
                                     'of_which_expected': sum(1 for x in scs if x.get('expected_alert') and any(w.get('callsign_repaired') for w in alerts_of(x['warnings'])))},
        'llm_parse_tagged_alerts': {'scenarios_with_one': sum(1 for x in scs if any(w.get('llm_parse') for w in alerts_of(x['warnings']))),
                                    'of_which_expected': sum(1 for x in scs if x.get('expected_alert') and any(w.get('llm_parse') for w in alerts_of(x['warnings'])))},
        'missed_readback (R7) false alarms': sum(1 for x in scs if cat(x, 'missed_readback') and 'R7' not in (x.get('expected_rules') or [])),
        'missed_readback (R7) scenarios expected / hit': [sum(1 for x in scs if 'R7' in (x.get('expected_rules') or [])), sum(1 for x in scs if 'R7' in (x.get('expected_rules') or []) and cat(x, 'missed_readback'))],
        'readback_mismatch (R14, its own category)': pr(lambda x: bool(cat(x, 'readback_mismatch')), 'expected_readback_mismatch'),
        'readback_mismatch_candidates (R14, shadow; vs wrong-runway readbacks)': pr(lambda x: any(w['rule_id'] == 'R14' for w in x['warnings']), 'expected_rb_candidate'),
        'incomplete_readback (R15, info; vs missing-runway readbacks)': pr(lambda x: any(w['rule_id'] == 'R15' for w in x['warnings']), 'expected_incomplete'),
        'low_confidence_mismatch (R16, info) scenarios': sum(1 for x in scs if any(w['rule_id'] == 'R16' for w in x['warnings'])),
        'unverified_conflict (R17, ADVISORY, its own category; N7) scenarios / of which expected': [
            sum(1 for x in scs if any(w['rule_id'] == 'R17' for w in x['warnings'])),
            sum(1 for x in scs if x.get('expected_alert') and any(w['rule_id'] == 'R17' for w in x['warnings']))]}
    extra = {'by_category': by_category,
             'llm_fallback': {'calls': len(llm), 'dropped_after_failed_validation': sum(1 for l in llm if l['dropped']),
                              'drop_reasons': dict(__import__('collections').Counter(l['reason'] for l in llm if l['dropped'])),
                              'no_actionable_content': sum(1 for l in llm if not l['dropped'] and not l['actionable'] and not l.get('unidentified')),
                              'unidentified_aircraft': sum(1 for l in llm if l.get('unidentified')), 'used': sum(1 for l in llm if l['actionable']),
                              'latency_s_median': round(statistics.median([l['latency_s'] for l in llm]), 3) if llm else None} if llm else None,
             'speaker': {'labelled': len(spk), 'hint_matches_label': sum(1 for r in spk if r.get('speaker_hint_used') == r['speaker_label']),
                         'hint_ambiguous_or_none': sum(1 for r in spk if r.get('speaker_hint_used') not in ('ATC', 'PILOT')),
                         'hint_wrong': sum(1 for r in spk if r.get('speaker_hint_used') in ('ATC', 'PILOT') and r['speaker_hint_used'] != r['speaker_label']),
                         'resolved_matches_label': sum(1 for r in spk if resolved_role(r) == r['speaker_label'])} if spk else None}
    return {'set': name, 'scenarios': len(scs), 'transmissions': len(rows), **fs, **extra,
            'alerts': {'scenarios_with_expectation': len(exp), 'tp': tp, 'fp': fp, 'fn': fn,
                       'precision': round(tp / (tp + fp), 3) if tp + fp else None, 'recall': round(tp / (tp + fn), 3) if tp + fn else None},
            'lead_time_s': {'n': len(leads), 'median': statistics.median(leads) if leads else None, 'min': min(leads) if leads else None, 'values': leads},
            'latency': {'library_ms_median': round(statistics.median(lib), 2) if lib else None, 'whisper_s_median': round(statistics.median(asr_s), 3) if asr_s else None,
                        'full_stack_clip_to_alert_s_median (E1, agent)': agent and agent.get('clip_to_alert_s_median')},
            'inputs': dict(sorted(__import__('collections').Counter(r['input'] for r in rows).items())),
            'kinds': sorted({s['kind'] for s in scs})}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--set', choices=('sim', 'klga_real', 'khwd_real', 'synthetic_dev', 'synthetic_heldout', 'synthetic_dev3', 'synthetic_heldout3', 'synthetic_dev4', 'synthetic_heldout4', 'all'), default='all')
    ap.add_argument('--text', choices=('asr', 'script'), default='asr', help='synthetic sets: Whisper text (default) or the clean script')
    ap.add_argument('--no-hints', action='store_true', help='synthetic sets: no speaker labels (as the runtime: it has none)')
    ap.add_argument('--runtime', action='store_true', help='round 3: emulate the deployed path (speaker classifier, 1 s clock, config readback timeout)')
    ap.add_argument('--traffic', choices=('perfect', 'realistic'), help='match callsigns against the scenario traffic list')
    ap.add_argument('--llm', action='store_true', help='local LLM parse for lines the grammar cannot type (calls the local vLLM)')
    ap.add_argument('--readback-timeout', type=float, help='override runtime_config readback_timeout_s')
    ap.add_argument('--tag', default='', help='suffix for the results file')
    ap.add_argument('--whisper-prompt', action='store_true', help='Whisper text produced with the initial prompt (*.asr_prompt.json)')
    ap.add_argument('--asr-words', action='store_true', help='round 4: Whisper prompt text WITH per-word confidence (*.asr_prompt_words.json)')
    ap.add_argument('--rb-threshold', type=float, help='round 4: readback-mismatch word-confidence threshold (default: runtime_config)')
    a = ap.parse_args(); global USE_SCRIPT, HINTS, RT, LLM, ASR_SUFFIX; ASR_SUFFIX = 'asr_prompt_words' if a.asr_words else 'asr_prompt' if a.whisper_prompt else 'asr'; USE_SCRIPT = a.text == 'script'; HINTS = not a.no_hints
    cfg = json.loads((ROOT / 'data/specs/runtime_config.json').read_text())
    if a.runtime:
        RT.update(hints='classifier', clock=True, timeout=a.readback_timeout or cfg['readback_timeout_s'], expiry=cfg.get('reservation_expiry_s'), hard_limit=cfg.get('reservation_hard_limit_s'),
                  rb_conf=a.rb_threshold if a.rb_threshold is not None else cfg.get('readback_mismatch_min_word_conf'))
    if a.traffic:
        RT['traffic'] = a.traffic
    if a.llm:
        RT.update(llm=True, llm_cfg={'base_url': cfg['llm']['base_url'], 'model': cfg['llm']['model'], 'max_tokens': cfg['llm']['max_tokens'], 'timeout': cfg['llm']['timeout_s']}); LLM = {}
    sets = ['sim', 'klga_real', 'khwd_real', 'synthetic_dev', 'synthetic_heldout'] if a.set == 'all' else [a.set]
    asr = asr_index(); RESULTS.mkdir(exist_ok=True); report = {}
    for name in sets:
        scs = set_sim(asr) if name == 'sim' else set_real(name) if name in ('klga_real', 'khwd_real') else set_synthetic(name.split('_', 1)[1])
        if not scs:
            print(f'{name}: no scenarios'); continue
        sm = summarize(name, scs); report[name] = sm
        name = name + ('_script' if USE_SCRIPT and name.startswith('synthetic') else '') + ('_nohints' if not HINTS and name.startswith('synthetic') else '') + \
            (('_wwords' if a.asr_words else '') + ('_wprompt' if a.whisper_prompt else '') + ('_runtime' if a.runtime else '') + (f'_traffic{a.traffic}' if a.traffic else '') + ('_llm' if a.llm else '') + a.tag if name.startswith('synthetic') else ''); sm['set'] = name
        (RESULTS / f'harness_{name}.json').write_text(json.dumps({'summary': sm, 'scenarios': scs}, indent=1, default=str) + '\n')
        al, ld, lt = sm['alerts'], sm['lead_time_s'], sm['latency']
        if name in ('klga_real', 'khwd_real'):
            sm['labels_needed'] = labels_needed(name, scs)
            (RESULTS / f'harness_{name}.json').write_text(json.dumps({'summary': sm, 'scenarios': scs}, indent=1, default=str) + '\n')
            print(f"[{name}] labels needed to be meaningful ({sm['labels_needed']['clips_in_set']} clips in the set):")
            for mk, mv in sm['labels_needed']['metrics'].items():
                print(f"  {mk}: {json.dumps(mv)}")
        print(f"[{name}] {' / '.join(sm['kinds'])}\n  {sm['scenarios']} scenarios, {sm['transmissions']} transmissions ({sm['scored']} scored, {sm['unscored']} UNSCORED); inputs {sm['inputs']}\n"
              f"  accuracy: callsign {sm['callsign_acc']}  runway {sm['runway_acc']}  instruction {sm['instruction_acc']}\n"
              f"  alerts: precision {al['precision']} recall {al['recall']} (tp {al['tp']} fp {al['fp']} fn {al['fn']} over {al['scenarios_with_expectation']} scenarios)\n"
              f"  warning lead time: median {ld['median']} s (n={ld['n']}, min {ld['min']})\n"
              f"  latency: library {lt['library_ms_median']} ms/transmission, Whisper {lt['whisper_s_median']} s/clip, full-stack agent clip->alert {lt['full_stack_clip_to_alert_s_median (E1, agent)']} s")
    (RESULTS / 'harness_summary.json').write_text(json.dumps(report, indent=1) + '\n')


if __name__ == '__main__':
    main()
