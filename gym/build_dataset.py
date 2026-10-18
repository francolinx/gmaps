"""Build a NeMo Gym task file for the GMAPS parser environment from a held-out split.

  python3 gym/build_dataset.py --split heldout5 --text script        # -> gym/environments/gmaps_parser/data/heldout5_script.jsonl
  python3 gym/build_dataset.py --split heldout4 --text asr            # Whisper (unprompted) text, the E1-path runtime input
  python3 gym/build_dataset.py --split heldout4 --text asr_prompt     # Whisper with the round-3 initial prompt

Each row: responses_create_params.input = the SAME system prompt the parser trains and serves with (llm_fallback SYSTEM +
airport schema + "Output JSON only.") and the user turn `Transmission: \"\"\"...\"\"\"`; plus gold, airport, transmission and
metadata for the verifier. Gold fields come from the split's truth.json; gold taxiways from the scripted text with the
callsign-aware rule (--gold-taxiways fixed, default; see DECISIONS N19) or the evaluator's current rule (legacy).
The policy sees only responses_create_params. This script is eval-side: it is the only place that reads the truth files.
"""
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SYN = REPO / 'eval/synthetic'
sys.path[:0] = [str(SYN), str(REPO / 'venue'), str(REPO / 'lib'), str(REPO / 'train')]
import llm_fallback as LF                                   # noqa: E402
from augment_asr_noise import airport_lists, spoken_taxiways_fixed, system_for   # noqa: E402
import train_ft as TF                                       # noqa: E402

OUT = REPO / 'gym/environments/gmaps_parser/data'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split', default='heldout5', choices=['heldout3', 'heldout4', 'heldout5', 'dev4'])
    ap.add_argument('--text', default='script', choices=['script', 'asr', 'asr_prompt'])
    ap.add_argument('--gold-taxiways', default='fixed', choices=['fixed', 'legacy'])
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    out = Path(a.out) if a.out else OUT / f'{a.split}_{a.text}{"" if a.gold_taxiways == "fixed" else "_legacygold"}.jsonl'
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, missing_text, src = [], 0, {}
    for icao in ('KLGA', 'KATL'):
        runways, vocab, prompt_src = airport_lists(icao); src[icao] = prompt_src
        fb = LF.LLMFallback(runways, vocab); sysmsg = system_for(fb)
        for sp in sorted((SYN / a.split / icao.lower()).glob('*.scenario.json')):
            sc = json.loads(sp.read_text()); tr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
            asr = {}
            if a.text != 'script':
                ap_ = sp.with_name(sp.name.replace('.scenario.json', f'.{a.text}.json'))
                if not ap_.exists():
                    raise SystemExit(f'{a.split} has no {a.text} text for {sp.name}: render + transcribe it first (eval/synthetic/asr.sh)')
                asr = json.loads(ap_.read_text())
            for tx, t in zip(sc['transmissions'], tr['transmissions']):
                f = t['fields']; cs = (f.get('callsigns') or [None])[0]
                gold = {'speaker_role': tx['speaker'] if tx['speaker'] in ('ATC', 'PILOT') else 'UNKNOWN', 'callsign': cs, 'runway': f.get('runway'),
                        'instruction': f['instruction'] if f['instruction'] in LF.KINDS else 'OTHER',
                        'taxiways': spoken_taxiways_fixed(tx['text'], vocab, cs) if a.gold_taxiways == 'fixed' else TF.spoken_taxiways(tx['text'], vocab)}
                text = tx['text'] if a.text == 'script' else (asr.get(tx['id']) or {}).get('text') or ''
                if not text:
                    missing_text += 1
                rows.append({'responses_create_params': {'input': [{'role': 'system', 'content': sysmsg},
                                                                    {'role': 'user', 'content': f'Transmission: """{text}"""'}]},
                             'gold': gold, 'airport': icao, 'transmission': text, 'src_id': tx['id'], 'split': a.split,
                             'text_source': a.text, 'gold_rule': a.gold_taxiways, 'SIMULATED': True})
    with out.open('w') as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(',', ':')) + '\n')
    commit = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    man_p = out.with_suffix('.manifest.json')
    man_p.write_text(json.dumps({'SIMULATED': True, 'split': a.split, 'text_source': a.text, 'gold_rule': a.gold_taxiways, 'rows': len(rows),
                                 'rows_with_empty_text': missing_text, 'prompt_source': src, 'built_at_commit': commit,
                                 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
                                 'note': 'eval-side file; the policy sees responses_create_params only; gold is for the verifier'}, indent=1) + '\n')
    print(f'{out}: {len(rows)} tasks ({missing_text} with empty text); prompt source {src}')


if __name__ == '__main__':
    main()
