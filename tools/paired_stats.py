"""Paired comparison of two (or more) systems on the SAME held-out records, from the offline runner's per-record output.

The scoreboard reports one proportion per system; this reports what the hand-off's "multi-run scoring" section asks for:
Wilson 95 % intervals per system, the PAIRED gain with a bootstrap 95 % interval (resampling records, so both systems move
together), the discordant counts and an exact McNemar test. No model, no GPU, CPU-only, a few seconds.

  python3 tools/paired_stats.py --set heldout4_asr \
      --ref gym/rollouts/offline_heldout4_asr_endpoint_base.json \
      --sys gym/rollouts/offline_heldout4_asr_endpoint_gmaps-parser.json gym/rollouts/offline_heldout4_asr_grammar.json
  python3 tools/paired_stats.py --all            # every set with an endpoint_base file, vs every other system file for that set
  python3 tools/paired_stats.py --all --md docs/PAIRED_STATS.md

Metric: `reward` (record exact) by default; `--metric accepted` uses record exact AND groundable ("correct and accepted").
Records are matched by `src_id`; a system missing a record is reported, never silently dropped. SIMULATED data throughout.
"""
import argparse, glob, json, math, os, random, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def wilson(k, n, z=1.959964):
    if n == 0:
        return (float('nan'), float('nan'))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def load(path, metric):
    d = json.load(open(path))
    rows = d.get('rows') or []
    out = {}
    for r in rows:
        ok = float(r.get('reward') or 0.0) >= 1.0
        if metric == 'accepted':
            ok = ok and float(r.get('groundable') or 0.0) >= 1.0
        out[r['src_id']] = 1 if ok else 0
    return d.get('summary', {}), out


def binom_two_sided(b, c):
    """Exact McNemar: two-sided binomial p-value for b successes in b+c trials at p=0.5."""
    m = b + c
    if m == 0:
        return 1.0
    k = min(b, c)
    cdf = sum(math.comb(m, i) for i in range(0, k + 1)) / 2 ** m
    return min(1.0, 2 * cdf)


def paired(ref, sys_, seed=20261017, boots=10000):
    ids = sorted(set(ref) & set(sys_))
    a = [ref[i] for i in ids]; b = [sys_[i] for i in ids]
    n = len(ids)
    ka, kb = sum(a), sum(b)
    both = sum(1 for x, y in zip(a, b) if x and y)
    only_ref = sum(1 for x, y in zip(a, b) if x and not y)
    only_sys = sum(1 for x, y in zip(a, b) if y and not x)
    neither = n - both - only_ref - only_sys
    diff = (kb - ka) / n if n else float('nan')
    rng = random.Random(seed)
    ds = []
    for _ in range(boots):
        s = 0
        for _ in range(n):
            j = rng.randrange(n)
            s += b[j] - a[j]
        ds.append(s / n)
    ds.sort()
    lo, hi = ds[int(0.025 * boots)], ds[int(0.975 * boots) - 1]
    return {'n': n, 'ref_k': ka, 'sys_k': kb, 'ref_rate': ka / n, 'sys_rate': kb / n,
            'ref_ci': wilson(ka, n), 'sys_ci': wilson(kb, n), 'gain_pts': 100 * diff,
            'gain_ci_pts': (100 * lo, 100 * hi), 'both': both, 'only_ref': only_ref, 'only_sys': only_sys,
            'neither': neither, 'mcnemar_p': binom_two_sided(only_sys, only_ref),
            'missing_in_sys': len(set(ref) - set(sys_)), 'missing_in_ref': len(set(sys_) - set(ref))}


def name_of(path):
    m = re.match(r'offline_(.+?)_(endpoint_)?([^_/]+(?:-[^_/]+)*)\.json$', os.path.basename(path))
    return m.group(3) if m else os.path.basename(path)


def fmt(x):
    return f'{x:.3f}'


def render(setname, metric, ref_path, rows):
    lines = [f'### {setname} ({"record exact" if metric == "reward" else "correct AND accepted"}; reference = {name_of(ref_path)}; paired on the same records)', '',
             '| system | n | rate [Wilson 95 %] | gain vs reference, pts [bootstrap 95 %] | sys right / ref wrong | ref right / sys wrong | McNemar p |',
             '|---|---|---|---|---|---|---|']
    for name, r in rows:
        lines.append(f"| {name} | {r['n']} | {fmt(r['sys_rate'])} [{fmt(r['sys_ci'][0])}, {fmt(r['sys_ci'][1])}] | "
                     f"{r['gain_pts']:+.1f} [{r['gain_ci_pts'][0]:+.1f}, {r['gain_ci_pts'][1]:+.1f}] | {r['only_sys']} | {r['only_ref']} | {r['mcnemar_p']:.2g} |"
                     + (f" missing {r['missing_in_sys']}/{r['missing_in_ref']}" if r['missing_in_sys'] or r['missing_in_ref'] else ''))
    if rows:
        r0 = rows[0][1]
        lines.append(f"| {name_of(ref_path)} (reference) | {r0['n']} | {fmt(r0['ref_rate'])} [{fmt(r0['ref_ci'][0])}, {fmt(r0['ref_ci'][1])}] | — | — | — | — |")
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', help='set name, e.g. heldout4_asr (for the heading)')
    ap.add_argument('--ref', help='reference system file (the baseline)')
    ap.add_argument('--sys', nargs='*', default=[], help='system files to compare with the reference')
    ap.add_argument('--all', action='store_true', help='every set that has an offline_<set>_endpoint_base.json in gym/rollouts')
    ap.add_argument('--metric', choices=['reward', 'accepted'], default='reward')
    ap.add_argument('--rollouts', default=str(REPO / 'gym/rollouts'))
    ap.add_argument('--md', help='also write the tables to this markdown file')
    ap.add_argument('--boots', type=int, default=10000)
    a = ap.parse_args(argv)
    blocks = []
    jobs = []
    if a.all:
        refs = sorted(glob.glob(os.path.join(a.rollouts, 'offline_*_endpoint_base.json')))
        setnames = [re.match(r'offline_(.+)_endpoint_base\.json$', os.path.basename(r)).group(1) for r in refs]
        for ref, setname in zip(refs, setnames):
            # heldout4_asr_* also matches heldout4_asr_prompt_*: drop files that belong to a longer set name
            longer = [s for s in setnames if s != setname and s.startswith(setname + '_')]
            systems = [p for p in sorted(glob.glob(os.path.join(a.rollouts, f'offline_{setname}_*.json')))
                       if p != ref and not p.endswith('_empty.json')
                       and not any(os.path.basename(p).startswith(f'offline_{s}_') for s in longer)]
            jobs.append((setname, ref, systems))
    else:
        if not (a.ref and a.sys):
            ap.error('--ref and --sys, or --all')
        jobs.append((a.set or 'set', a.ref, a.sys))
    for setname, ref, systems in jobs:
        _, refrows = load(ref, a.metric)
        rows = []
        for s in systems:
            _, srows = load(s, a.metric)
            rows.append((name_of(s), paired(refrows, srows, boots=a.boots)))
        blocks.append(render(setname, a.metric, ref, rows))
    text = ('# Paired statistics on the held-out sets (SIMULATED; generated by tools/paired_stats.py)\n\n'
            'Same records for every system in a table, so the gain and its bootstrap interval are paired. Wilson intervals per system. '
            'McNemar: exact two-sided test on the discordant pairs. Record exact = all five fields match the scripted gold.\n\n'
            + '\n\n'.join(blocks) + '\n')
    print(text)
    if a.md:
        Path(a.md).write_text(text)
        print(f'-> {a.md}', file=sys.stderr)


if __name__ == '__main__':
    main()
