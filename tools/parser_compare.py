"""Parser comparison on a demo scenario's own lines (Oct 18): grammar / untuned Qwen3-4B / fine-tuned gmaps-parser, one record per line,
written to ui_scaffold/samples/parsers_<episode>.json for demo.html (display only; nothing here is tuned or fed back).

  python3 tools/parser_compare.py LGA_2026-03-22                 # fixture rows with -40 <= t <= 48, KLGA chart identifiers
  python3 tools/parser_compare.py SIM_E3_STOP_NOT_OBSERVED       # fictional XSIM, the script lines as text (no audio path used)

Accepted: grammar iff parse_path == 'grammar' and speech_act != 'unknown'; a model iff the runtime fallback (venue/llm_fallback.py,
unchanged) returns fields and fields.get('actionable', True). The radio text is data: it only goes into the fallback's user message.
"""
import argparse, datetime, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'venue'), str(ROOT / 'eval/synthetic')]
from gmaps_core.parser import parse                            # noqa: E402
from llm_fallback import LLMFallback                           # noqa: E402

MODELS = {'base': 'Qwen3-4B-Instruct-2507 (untuned)',
          'gmaps-parser': 'gmaps-parser: Qwen3-4B-Instruct-2507 + LoRA, run gmaps-parser-4b-20261017-034510'}
LGA_TRAFFIC = ['JZA646', 'TRUCK1', 'UAL2384', 'DAL520', 'FFT4195', 'DAL263']
SIM_TRAFFIC = ['SIM212', 'RESCUE7']


def lines_for(ep):
    d = json.loads((ROOT / f'ui_scaffold/samples/{ep}.json').read_text())
    if 'timeline' in d:   # historical fixture: caption text, no audio
        return [{'t': r['t'], 'text': r['text']} for r in d['timeline'] if -40 <= r['t'] <= 48], 'KLGA'
    return [{'t': r['t'], 'text': r['text']} for r in d['transcript']], 'XSIM'


def grammar(text, airport):
    e = parse(text, airport)
    ok = e.get('parse_path') == 'grammar' and e.get('speech_act') != 'unknown'
    return {'accepted': ok, 'fields': {'callsign': (e.get('actor_candidates') or [None])[0], 'instruction': e.get('kind')},
            'speech_act': e.get('speech_act'), 'parse_path': e.get('parse_path')}


def main():
    a = argparse.ArgumentParser(); a.add_argument('episode'); a.add_argument('--base-url', default='http://127.0.0.1:8001/v1')
    args = a.parse_args()
    lines, airport = lines_for(args.episode)
    if airport == 'KLGA':
        from chart_ids import ChartIdentifiers
        A = ChartIdentifiers('KLGA')
        runways, taxiways, traffic = sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab), LGA_TRAFFIC
        source = 'caption lines (text) → grammar; models on 127.0.0.1:8001 with the KLGA chart identifiers and the traffic list'
    else:
        runways, taxiways, traffic = ['18', '36'], ['K'], SIM_TRAFFIC
        source = ('script lines as text (the Whisper worker path needs a second GPU container; not used tonight) → grammar; '
                  'models on 127.0.0.1:8001, XSIM runways 18/36, taxiway K, traffic list SIM212/RESCUE7')
    fbs = {m: LLMFallback(runways, taxiways, base_url=args.base_url, model=m) for m in MODELS}
    rows = []
    for ln in lines:
        row = {'t': ln['t'], 'script': ln['text'], 'whisper': ln['text'], 'grammar': grammar(ln['text'], airport)}
        for m, fb in fbs.items():
            fields, info = fb.parse(ln['text'], traffic=traffic)
            row[m] = {'accepted': fields is not None and fields.get('actionable', True), 'fields': fields,
                      'reason': info['reason'], 'latency_s': info['latency_s']}
        rows.append(row)
        print(f"{ln['t']:+4d} g={'Y' if row['grammar']['accepted'] else 'n'} base={'Y' if row['base']['accepted'] else 'n'}"
              f"({row['base']['reason'][:40]}, {row['base']['latency_s']}s) ft={'Y' if row['gmaps-parser']['accepted'] else 'n'}"
              f"({row['gmaps-parser']['reason'][:40]}, {row['gmaps-parser']['latency_s']}s)  {ln['text'][:60]}")
    out = {'episode': args.episode, 'generated': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
           'source': source, 'models': MODELS, 'rows': rows}
    p = ROOT / f'ui_scaffold/samples/parsers_{args.episode}.json'
    p.write_text(json.dumps(out, indent=1) + '\n')
    n = len(rows)
    print(f"-> {p.relative_to(ROOT)}: n={n} grammar {sum(r['grammar']['accepted'] for r in rows)}/{n} "
          f"base {sum(r['base']['accepted'] for r in rows)}/{n} gmaps-parser {sum(r['gmaps-parser']['accepted'] for r in rows)}/{n}")


if __name__ == '__main__':
    main()
