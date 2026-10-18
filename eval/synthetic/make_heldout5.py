"""heldout5: a FRESH held-out text set, generated AFTER the N17 change was frozen (commit 51aee83).
SIMULATED. Text only (no TTS / ASR). Reuses generate_v4.build() unchanged (written to a scratch dir, then collected), with
the held-out phrasing pool E and NEW seeds 7601-7603 per airport (heldout4 used 7501). Evaluation only; never used for tuning.

  python3 eval/synthetic/make_heldout5.py      # -> eval/synthetic/heldout5/{katl,klga}/*.scenario.json + *.truth.json + MANIFEST.json
"""
import json, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE)]
import generate_v4 as G      # noqa: E402

OUT = HERE / 'heldout5'
SEEDS = [7601, 7602, 7603]


def norm(t):
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', t.lower()).split())


def texts(split):
    return {norm(tx['text']) for p in (HERE / split).glob('*/*.scenario.json') for tx in json.loads(p.read_text())['transmissions']}


def main():
    G.POOL['heldout5'] = ['E']
    commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    counts = {}
    with tempfile.TemporaryDirectory() as td:
        G.ROOT = Path(td)
        for icao in ('KATL', 'KLGA'):
            dest = OUT / icao.lower(); dest.mkdir(parents=True, exist_ok=True)
            for seed in SEEDS:
                G.build(icao, seed, 'heldout5')
                for sp in sorted((Path(td) / 'eval/synthetic/heldout5' / icao.lower()).glob('*.scenario.json')):
                    sc = json.loads(sp.read_text()); tr = json.loads(sp.with_name(sp.name.replace('.scenario.json', '.truth.json')).read_text())
                    sid = sc['id'].replace('_HELDOUT5_', f'_HELDOUT5_S{seed}_')
                    fix = lambda i: i.replace('_HELDOUT5_', f'_HELDOUT5_S{seed}_')
                    sc.update(id=sid, transmissions=[dict(t, id=fix(t['id'])) for t in sc['transmissions']])
                    tr.update(id=sid, transmissions=[dict(t, id=fix(t['id'])) for t in tr['transmissions']])
                    (dest / f'{sid}.scenario.json').write_text(json.dumps(sc, indent=1) + '\n')
                    (dest / f'{sid}.truth.json').write_text(json.dumps(tr, indent=1) + '\n')
                    c = counts.setdefault(icao, {'scenarios': 0, 'transmissions': 0})
                    c['scenarios'] += 1; c['transmissions'] += len(sc['transmissions'])
    h5 = texts('heldout5')
    man = {'SIMULATED': True, 'EVALUATION_ONLY': True, 'text_only': True, 'generated_after_frozen_commit': commit,
           'generator': 'eval/synthetic/generate_v4.py build() via eval/synthetic/make_heldout5.py', 'phrasing_pool': 'E', 'seeds': SEEDS,
           'counts': counts, 'unique_normalized_texts': len(h5),
           'text_overlap_normalized': {'train_ft': len(h5 & texts('train_ft')), 'heldout4': len(h5 & texts('heldout4')),
                                       'heldout3': len(h5 & texts('heldout3')), 'dev4': len(h5 & texts('dev4'))},
           'note': 'Same pool E templates as heldout4 (no new 7110.65 realizations exist); new seeds give new aircraft, runways, taxiways and draws.'}
    (OUT / 'MANIFEST.json').write_text(json.dumps(man, indent=1) + '\n')
    print(json.dumps(man, indent=1))


if __name__ == '__main__':
    main()
