"""N17 before/after. SIMULATED. Reads heldout4 / heldout5 only.

One call to the running 35B per transmission (venue/llm_fallback.LLMFallback.parse, unchanged endpoint); the SAME raw JSON is then
validated by the pre-N17 validator (venue/llm_fallback.py at commit 9fc3dcb, loaded from git) and by the frozen N17 validator
(working tree, commit 51aee83). Any difference is the validator's alone, not model variance.

  python3 eval/n17_before_after.py     # -> eval/results/n17_before_after.json
"""
import importlib.util, json, statistics, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'eval'), str(ROOT / 'eval/synthetic'), str(ROOT / 'venue'), str(ROOT / 'lib')]
import llm_fallback as NEW                                  # noqa: E402  (frozen N17)
from chart_ids import ChartIdentifiers                      # noqa: E402
from parser_baselines import gold_of, match, FIELDS, pct    # noqa: E402

PRE_N17 = '9fc3dcb'


def load_old():
    src = subprocess.run(['git', '-C', str(ROOT), 'show', f'{PRE_N17}:venue/llm_fallback.py'], capture_output=True, text=True, check=True).stdout
    f = Path(tempfile.mkdtemp()) / 'llm_fallback_pre_n17.py'
    f.write_text(src)
    spec = importlib.util.spec_from_file_location('llm_fallback_pre_n17', f)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def items(split, text_source):
    out = []
    for icao in ('KATL', 'KLGA'):
        A = ChartIdentifiers(icao)
        for sp in sorted((ROOT / 'eval/synthetic' / split / icao.lower()).glob('*.scenario.json')):
            sc = json.loads(sp.read_text()); tr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
            asr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.asr.json')).read_text()) if text_source == 'whisper' else {}
            for tx, t in zip(sc['transmissions'], tr['transmissions']):
                gold = gold_of(tx, t, sorted(A.vocab))
                text = (asr.get(tx['id']) or {}).get('text') or '' if text_source == 'whisper' else tx['text']
                out.append((icao, A, tx['id'], text, gold))
    return out


def summarize(rows, key):
    n = len(rows)
    acc = [r for r in rows if r[key] is not None]
    reasons = {}
    for r in rows:
        reasons[r[key + '_reason']] = reasons.get(r[key + '_reason'], 0) + 1
    return {'n': n, 'drop_rate': round(sum(r[key] is None for r in rows) / n, 3),
            'accepted_actionable_rate': round(sum(bool(r[key] and r[key].get('actionable')) for r in rows) / n, 3),
            'field_exact_match': {k: round(sum(match(r[key], r['gold'])[k] for r in rows) / n, 3) for k in FIELDS},
            'callsign_runway_instruction_all_right': round(sum(all(match(r[key], r['gold'])[k] for k in ('callsign', 'runway', 'instruction')) for r in rows) / n, 3),
            'reasons': dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}


def run(split, text_source, OLD):
    its = items(split, text_source)
    fbn = {i: NEW.LLMFallback(sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab)) for i, A, *_ in its}
    fbo = {i: OLD.LLMFallback(sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab)) for i, A, *_ in its}
    rows, lat = [], []
    for icao, A, tid, text, gold in its:
        _, info = fbn[icao].parse(text)
        lat.append(info['latency_s'])
        try:
            raw = json.loads(info.get('raw') or 'null')
        except Exception:
            raw = None
        if isinstance(raw, dict):
            new_f, new_r = fbn[icao].validate(raw, text)
            old_f, old_r = fbo[icao].validate(raw, text)
        else:
            new_f = old_f = None; new_r = old_r = info['reason']
        rows.append({'id': tid, 'gold': gold, 'old': old_f, 'old_reason': old_r, 'new': new_f, 'new_reason': new_r})
    changed = [r['id'] for r in rows if (r['old'] is None) != (r['new'] is None)]
    regress = [r['id'] for r in rows if r['old'] is not None and r['new'] is None]
    return {'n': len(rows), 'before_pre_N17': summarize(rows, 'old'), 'after_N17': summarize(rows, 'new'),
            'decisions_changed': len(changed), 'newly_dropped_by_N17': len(regress),
            'latency_s': {'median': round(statistics.median(lat), 3), 'p95': pct(lat, 95)}}


def main():
    OLD = load_old()
    res = {'SIMULATED': True, 'generated': time.strftime('%Y-%m-%d %H:%M %Z'), 'model': 'running 35B via llm_fallback defaults (127.0.0.1:8000)',
           'before': f'venue/llm_fallback.py @ {PRE_N17} (pre-N17)', 'after': 'venue/llm_fallback.py @ 51aee83 (N17, frozen before heldout5 existed)',
           'note': 'Same raw model JSON validated twice per transmission. Field match over all n (a drop scores 0). No traffic list.',
           'heldout4_script': run('heldout4', 'script', OLD), 'heldout4_whisper': run('heldout4', 'whisper', OLD), 'heldout5_script': run('heldout5', 'script', OLD)}
    (ROOT / 'eval/results/n17_before_after.json').write_text(json.dumps(res, indent=1) + '\n')
    for k in ('heldout4_script', 'heldout4_whisper', 'heldout5_script'):
        r = res[k]
        for side in ('before_pre_N17', 'after_N17'):
            s = r[side]
            print(f"{k:17s} {side:15s} n {s['n']} drop {s['drop_rate']} csgn/rwy/instr {s['field_exact_match']['callsign']}/{s['field_exact_match']['runway']}/{s['field_exact_match']['instruction']} all3 {s['callsign_runway_instruction_all_right']}")
        print(f"{'':17s} decisions changed {r['decisions_changed']}, newly dropped by N17 {r['newly_dropped_by_N17']}, latency {r['latency_s']}")


if __name__ == '__main__':
    main()
