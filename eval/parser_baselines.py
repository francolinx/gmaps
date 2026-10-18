"""Parser baselines on the heldout4 TEXT. SIMULATED synthetic phraseology. Reads eval/synthetic/heldout4 only
(never writes there); changes no parser, ledger or fallback code.

  python3 eval/parser_baselines.py                          # -> eval/results/parser_baselines.json (gold taxiways: legacy rule, unchanged)
  python3 eval/parser_baselines.py --gold-taxiways fixed    # DECISIONS N19: callsign-aware gold -> eval/results/parser_baselines_fixedgold.json

(a) grammar alone: classify_speaker + parse(text, airport, resolver=chart identifiers), exactly as the evaluation/deployed server.
    "typed" = the fast path's own test (grammar path, kind not OTHER/UNKNOWN, a callsign).
(b) the running local 35B through venue/llm_fallback.LLMFallback, unchanged: its default endpoint/model, no traffic list.
Per-field exact match against the generator's per-transmission truth (callsign, runway, instruction), the scenario's speaker,
and gold taxiways = chart taxiway names spoken in the text (same rule as eval/synthetic/train_ft.py).
Field match is counted over ALL n transmissions (an untyped / dropped transmission scores 0 on every field).
Two inputs, reported separately: the scripted text, and the Whisper large-v3-turbo transcript of the same transmission
(<scenario>.asr.json, no prompt: the runtime input on the E1 path). Gold taxiways always come from the scripted text.
"""
import json, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'eval/synthetic'), str(ROOT / 'venue'), str(ROOT / 'lib')]
from gmaps_core.parser import parse, classify_speaker   # noqa: E402
from gmaps_core.normalize import canonical_runway       # noqa: E402
from chart_ids import ChartIdentifiers                  # noqa: E402
from train_ft import spoken_taxiways                    # noqa: E402
import llm_fallback as LF                               # noqa: E402

FIELDS = ('speaker_role', 'callsign', 'runway', 'instruction', 'taxiways')
GOLD_RULE = 'legacy'   # 'fixed' = train/augment_asr_noise.spoken_taxiways_fixed (callsign letters are not taxiways; DECISIONS N19)


def gold_taxiways(text, vocab, callsign):
    if GOLD_RULE == 'fixed':
        sys.path.insert(0, str(ROOT / 'train'))
        from augment_asr_noise import spoken_taxiways_fixed
        return spoken_taxiways_fixed(text, vocab, callsign)
    return spoken_taxiways(text, vocab)


def rw(x):
    return (canonical_runway(x) or str(x).lstrip('0')) if x else None


def gold_of(tx, t, vocab):
    f = t['fields']
    return {'speaker_role': tx['speaker'], 'callsign': (f.get('callsigns') or [None])[0], 'runway': rw(f.get('runway')),
            'instruction': f['instruction'] if f['instruction'] in LF.KINDS else 'OTHER',
            'taxiways': gold_taxiways(tx['text'], vocab, (f.get('callsigns') or [None])[0])}


def match(pred, gold):
    if pred is None:
        return {k: False for k in FIELDS}
    p = dict(pred, runway=rw(pred.get('runway')))
    return {k: (p.get(k) == gold[k]) if k != 'taxiways' else (list(p.get(k) or []) == gold[k]) for k in FIELDS}


def summarize(rows, extra=None):
    n = len(rows)
    out = {'n': n, 'field_exact_match': {k: round(sum(r['match'][k] for r in rows) / n, 3) for k in FIELDS},
           'all_fields_exact': round(sum(all(r['match'].values()) for r in rows) / n, 3)}
    out.update(extra or {})
    return out


def pct(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))], 3) if v else None


def run(text_source):
    items = []
    for icao in ('KATL', 'KLGA'):
        A = ChartIdentifiers(icao)
        for sp in sorted((ROOT / 'eval/synthetic/heldout4' / icao.lower()).glob('*.scenario.json')):
            sc = json.loads(sp.read_text()); tr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
            asr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.asr.json')).read_text()) if text_source == 'whisper' else {}
            for tx, t in zip(sc['transmissions'], tr['transmissions']):
                gold = gold_of(tx, t, sorted(A.vocab))
                if text_source == 'whisper':
                    tx = dict(tx, text=(asr.get(tx['id']) or {}).get('text') or '')
                items.append((icao, A, tx, gold))
    # (a) grammar alone
    ga = []
    for icao, A, tx, gold in items:
        role, _ = classify_speaker(tx['text'])
        ev = parse(tx['text'], icao, speaker_hint=role, resolver=A, event_time=tx['t'], source={'id': tx['id'], 'start_s': tx['t'], 'end_s': None})
        typed = ev.get('parse_path') == 'grammar' and ev['kind'] not in ('OTHER', 'UNKNOWN', None) and bool(ev.get('actor_candidates'))
        twy = [x for x in ([ev.get('crossing_at')] if ev.get('crossing_at') else []) + list(ev.get('route') or []) if x]
        pred = {'speaker_role': role if role in ('ATC', 'PILOT') else 'UNKNOWN', 'callsign': (ev.get('actor_candidates') or [None])[0],
                'runway': ev.get('runway'), 'instruction': ev['kind'], 'taxiways': twy[:4]} if typed else None
        ga.append({'id': tx['id'], 'typed': typed, 'match': match(pred, gold)})
    # (b) the running 35B fallback, unchanged
    fbs = {icao: LF.LLMFallback(sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab)) for icao, A in
           {i: a for i, a, _, _ in items}.items()}
    lb, lat, reasons = [], [], {}
    for icao, A, tx, gold in items:
        fields, info = fbs[icao].parse(tx['text'])
        lat.append(info['latency_s'])
        reasons[info['reason']] = reasons.get(info['reason'], 0) + 1
        raw = None
        try:
            raw = json.loads(info.get('raw') or 'null')
        except Exception:
            pass
        accepted = fields if fields is not None else None
        lb.append({'id': tx['id'], 'dropped': fields is None, 'actionable': bool(fields and fields.get('actionable')),
                   'match': match(accepted, gold), 'raw_match': match(raw if isinstance(raw, dict) else None, gold)})
    n = len(lb)
    return {'grammar_alone': summarize(ga, {'typed_rate': round(sum(r['typed'] for r in ga) / len(ga), 3)}),
           'llm_fallback_35b': summarize(lb, {
               'endpoint': 'venue/llm_fallback.py defaults (127.0.0.1:8000, nvidia/Qwen3.6-35B-A3B-NVFP4), unchanged; traffic list: none',
               'drop_rate': round(sum(r['dropped'] for r in lb) / n, 3),
               'accepted_actionable_rate': round(sum(r['actionable'] for r in lb) / n, 3),
               'reasons': dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
               'raw_json_field_exact_match': {k: round(sum(r['raw_match'][k] for r in lb) / n, 3) for k in FIELDS},
               'latency_s': {'median': round(statistics.median(lat), 3), 'p95': pct(lat, 95), 'max': round(max(lat), 3)}})}


def main():
    global GOLD_RULE
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--gold-taxiways', default='legacy', choices=['legacy', 'fixed'],
                    help="legacy = unchanged rule (default); fixed = callsign-aware rule from train/augment_asr_noise.py (DECISIONS N19)")
    GOLD_RULE = ap.parse_args().gold_taxiways
    res = {'SIMULATED': True, 'set': 'eval/synthetic/heldout4 (KATL + KLGA), read only', 'generated': time.strftime('%Y-%m-%d %H:%M %Z'),
           'gold_taxiway_rule': GOLD_RULE,
           'note': 'Field match over all n; untyped (grammar) or dropped (LLM) transmissions score 0 on every field. Gold taxiways = chart names spoken in the scripted text'
                   + (' (callsign letters excluded, N19).' if GOLD_RULE == 'fixed' else ' (legacy rule).'),
           'script_text': run('script'), 'whisper_text': run('whisper')}
    res['grammar_alone'], res['llm_fallback_35b'] = res['script_text']['grammar_alone'], res['script_text']['llm_fallback_35b']   # the requested entries (script text)
    out = ROOT / ('eval/results/parser_baselines.json' if GOLD_RULE == 'legacy' else 'eval/results/parser_baselines_fixedgold.json')
    out.write_text(json.dumps(res, indent=1) + '\n')
    for src in ('script_text', 'whisper_text'):
        for k in ('grammar_alone', 'llm_fallback_35b'):
            r = res[src][k]
            print(src, k, 'n', r['n'], 'fields', r['field_exact_match'], 'all', r['all_fields_exact'],
                  {x: r[x] for x in ('typed_rate', 'drop_rate', 'latency_s', 'reasons') if x in r})


if __name__ == '__main__':
    main()
