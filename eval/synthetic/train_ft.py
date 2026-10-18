"""Text-only TRAINING split for the parser fine-tune. SIMULATED. No TTS, no ASR, no real audio.

  python3 eval/synthetic/train_ft.py            # -> eval/synthetic/train_ft/{klga,katl}/, pairs.jsonl, MANIFEST.json

Reuses generate_v4.build() unchanged (written to a scratch directory, then collected), with FRESH seeds and only the dev
phrasing pools C and D. Held-out4's pool E is never used. Every transmission gets a target in the exact
venue/llm_fallback.py schema {speaker_role, callsign, runway, instruction, taxiways}:
  speaker_role  ATC / PILOT, from the scenario
  callsign      the generator's truth callsign (ICAO airline code + digits, or an N-registration)
  runway        the generator's truth runway (None where it is not spoken)
  instruction   the generator's truth kind (all in llm_fallback.KINDS)
  taxiways      chart taxiway names actually spoken in the text (matched with the generator's own say_twy), in order, max 4
pairs.jsonl is chat format. The system prompt is llm_fallback's SYSTEM + this airport's JSON schema + "Output JSON only." The user turn
is the transmission exactly as llm_fallback sends it (Transmission: \"\"\"...\"\"\"), so training matches serving. The assistant
turn is the gold JSON. Any transmission whose text also occurs in heldout3 or heldout4 is excluded; the overlap after
exclusion is asserted to be 0.
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / 'venue'), str(ROOT / 'lib')]
import generate_v4 as G                                   # noqa: E402
from generate import say_twy                              # noqa: E402
from chart_ids import ChartIdentifiers                    # noqa: E402
import llm_fallback as LF                                 # noqa: E402

OUT = HERE / 'train_ft'
AIRPORTS = ('KLGA', 'KATL')
SEEDS = list(range(31001, 31041))                          # fresh: earlier sets used 9101-9301 (dev) and 7401/7501 (held-out)
MIN_TX = 1500


def norm(t):
    return re.sub(r'[^a-z0-9 ]', '', t.lower()).split()


def heldout_texts():
    ex, nm = set(), set()
    for split in ('heldout3', 'heldout4'):
        for p in (HERE / split).glob('*/*.scenario.json'):
            for tx in json.loads(p.read_text())['transmissions']:
                ex.add(tx['text']); nm.add(' '.join(norm(tx['text'])))
    return ex, nm


def spoken_taxiways(text, vocab):
    words, hits = norm(text), []
    for t in vocab:
        sw = norm(say_twy(t))
        for i in range(len(words) - len(sw) + 1):
            if words[i:i + len(sw)] == sw:
                hits.append((i, -len(sw), t))
    out, used = [], set()
    for i, neg, t in sorted(hits):                         # earliest first; at one position the longest spoken form wins
        span = set(range(i, i - neg))
        if span & used:
            continue
        used |= span
        out.append(t)
    return out[:4]


def system_for(fb):
    return fb.system + '\nJSON schema (allowed values for this airport):\n' + json.dumps(fb.schema, separators=(',', ':')) + '\nOutput JSON only.'


def main():
    G.POOL['train_ft'] = ['C', 'D']
    ex_held, nm_held = heldout_texts()
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    pairs, counts, dropped, kinds = [], {}, {'exact_overlap': 0, 'normalized_overlap': 0}, {}
    with tempfile.TemporaryDirectory() as td:
        G.ROOT = Path(td)                                   # build() writes under G.ROOT/eval/synthetic/<split>/<icao>/
        for icao in AIRPORTS:
            A = ChartIdentifiers(icao)
            fb = LF.LLMFallback(sorted({e['identifier'] for e in A.ends.values()}), sorted(A.vocab))
            sysmsg = system_for(fb)
            dest = OUT / icao.lower(); dest.mkdir(parents=True, exist_ok=True)
            n_tx = 0
            for seed in SEEDS:
                if n_tx >= (MIN_TX + 1) // 2 + 100:
                    break
                G.build(icao, seed, 'train_ft')
                src = Path(td) / 'eval/synthetic/train_ft' / icao.lower()
                for sp in sorted(src.glob('*.scenario.json')):
                    sc = json.loads(sp.read_text()); tr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
                    sid = sc['id'].replace('_TRAIN_FT_', f'_TRAINFT_S{seed}_')
                    keep_tx, keep_tr = [], []
                    for tx, t in zip(sc['transmissions'], tr['transmissions']):
                        if tx['text'] in ex_held:
                            dropped['exact_overlap'] += 1; continue
                        if ' '.join(norm(tx['text'])) in nm_held:
                            dropped['normalized_overlap'] += 1; continue
                        f = t['fields']
                        target = {'speaker_role': tx['speaker'] if tx['speaker'] in ('ATC', 'PILOT') else 'UNKNOWN',
                                  'callsign': (f.get('callsigns') or [None])[0], 'runway': f.get('runway'),
                                  'instruction': f['instruction'] if f['instruction'] in LF.KINDS else 'OTHER',
                                  'taxiways': spoken_taxiways(tx['text'], sorted(A.vocab))}
                        assert target['runway'] is None or target['runway'] in fb.runways, (icao, target)
                        assert all(x in fb.taxiways for x in target['taxiways'])
                        tid = tx['id'].replace('_TRAIN_FT_', f'_TRAINFT_S{seed}_')
                        keep_tx.append(dict(tx, id=tid)); keep_tr.append({'id': tid, 'fields': f, 'target': target})
                        pairs.append({'id': tid, 'airport': icao, 'messages': [
                            {'role': 'system', 'content': sysmsg},
                            {'role': 'user', 'content': f'Transmission: """{tx["text"]}"""'},
                            {'role': 'assistant', 'content': json.dumps(target, separators=(',', ':'))}]})
                        kinds[target['instruction']] = kinds.get(target['instruction'], 0) + 1
                    if not keep_tx:
                        continue
                    sc.update(id=sid, split='train_ft', transmissions=keep_tx,
                              note=sc['note'] + ' TRAINING split (text only, no TTS); never used for evaluation.')
                    (dest / f'{sid}.scenario.json').write_text(json.dumps(sc, indent=1) + '\n')
                    (dest / f'{sid}.truth.json').write_text(json.dumps({'id': sid, 'TRAINING_ONLY': True, 'transmissions': keep_tr}, indent=1) + '\n')
                    n_tx += len(keep_tx)
                    counts.setdefault(icao, {'scenarios': 0, 'transmissions': 0, 'seeds': []})
                    counts[icao]['scenarios'] += 1; counts[icao]['transmissions'] += len(keep_tx)
                    if seed not in counts[icao]['seeds']:
                        counts[icao]['seeds'].append(seed)
    texts = [p['messages'][1]['content'][len('Transmission: """'):-3] for p in pairs]
    overlap_exact = sum(1 for t in texts if t in ex_held)
    overlap_norm = sum(1 for t in texts if ' '.join(norm(t)) in nm_held)
    assert overlap_exact == 0 and overlap_norm == 0, (overlap_exact, overlap_norm)
    assert len(pairs) >= MIN_TX, len(pairs)
    with (OUT / 'pairs.jsonl').open('w') as f:
        for p in pairs:
            f.write(json.dumps(p, separators=(',', ':')) + '\n')
    man = {'SIMULATED': True, 'TRAINING_ONLY': True, 'text_only': True, 'generator': 'eval/synthetic/generate_v4.py build() via eval/synthetic/train_ft.py',
           'generator_commit': commit, 'phrasing_pools': ['C', 'D'], 'heldout_pool_E_used': False, 'seeds': {k: v['seeds'] for k, v in counts.items()},
           'counts': {k: {'scenarios': v['scenarios'], 'transmissions': v['transmissions']} for k, v in counts.items()},
           'transmissions_total': len(pairs), 'instruction_counts': dict(sorted(kinds.items())),
           'unique_texts': len(set(texts)), 'excluded_for_heldout_overlap': dropped,
           'overlap_with_heldout3_heldout4_after_exclusion': {'exact': overlap_exact, 'normalized': overlap_norm},
           'target_schema': 'venue/llm_fallback.py {speaker_role, callsign, runway, instruction, taxiways}',
           'pairs_format': 'chat: system = llm_fallback SYSTEM + airport JSON schema + "Output JSON only."; user = Transmission: """text""" (as llm_fallback sends); assistant = gold JSON'}
    import hashlib
    man['pairs_jsonl'] = {'sha256': hashlib.sha256((OUT / 'pairs.jsonl').read_bytes()).hexdigest(), 'bytes': (OUT / 'pairs.jsonl').stat().st_size,
                          'tracked_in_git': False, 'why': 'over the 5 MB pre-commit limit (the system prompt repeats per row); regenerate with this script (deterministic)'}
    man['generator_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    man['generator_commit_note'] = 'generator_commit = HEAD when generated; train_ft.py itself lands in the next commit (see generator_script_sha256)'
    (OUT / 'MANIFEST.json').write_text(json.dumps(man, indent=1) + '\n')
    print(json.dumps({k: man[k] for k in ('counts', 'transmissions_total', 'unique_texts', 'excluded_for_heldout_overlap', 'overlap_with_heldout3_heldout4_after_exclusion', 'instruction_counts', 'seeds')}, indent=1))
    print(f"overlap: {overlap_exact}")


if __name__ == '__main__':
    main()
