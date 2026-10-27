"""Charts for the release bundle's model and dataset cards. Every drawn value is read from result files, never typed:
held-out rows through tools/scoreboard.py's loaders (gym/rollouts/offline_*.json, *_by_set.json), the second-machine rows from
eval/results/brev/, the paired statistics through tools/paired_stats.py, the demo-line counts from ui_scaffold/samples/parsers_*.json
(typed right = the demo page's rule: accepted, and callsign + instruction match the scenario's ledger line), the dataset counts from
eval/synthetic/train_ft/MANIFEST.json.

  python3 tools/release_charts.py --out <bundle>/assets --dataset-out <bundle>/dataset/assets
  python3 tools/release_charts.py --out <bundle>/assets --verify      # + every drawn label against docs/SCOREBOARD.md -> CHARTS OK 7/7

matplotlib is needed (the system python3 has none; ~/gymvenv313/bin/python has it, or set CHARTS_PYTHON). SIMULATED data.
"""
import argparse, importlib.util, json, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit('matplotlib missing: run with ~/gymvenv313/bin/python (or CHARTS_PYTHON=<python with matplotlib>)')


def _mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


SB = _mod('scoreboard_rc', 'tools/scoreboard.py')
PS = _mod('paired_stats_rc', 'tools/paired_stats.py')

GREY, BLUE, GREEN, AMBER, BLACK = '#6b7280', '#2563eb', '#16a34a', '#d97706', '#111111'
COLOR = {'base': GREY, 'grammar': GREEN, 'gmaps-parser': BLUE, 'gmaps-parser-full': AMBER}
NAME = {'base': 'untuned base (Qwen3-4B-Instruct-2507)', 'grammar': 'grammar (hand-written, no model)',
        'gmaps-parser': 'gmaps-parser (ours: base + LoRA)', 'gmaps-parser-full': 'full-data adapter (not submitted)'}
SETS = [('heldout4_asr', 'Whisper text'), ('heldout4_asr_prompt', 'Whisper text +\nvocabulary prompt'), ('heldout5_script', 'blind scripted')]
FOOTER = 'SIMULATED data. Same verifier for every system.'
DATE_RX = re.compile(r'2026|20[0-9]{6}|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? [0-9]')
LABELS = []   # (chart, text, value, decimals, key) for --verify
TEXTS = []    # every title / axis / tick text, checked for dates

plt.rcParams.update({'font.size': 15, 'axes.titlesize': 18, 'axes.labelsize': 15, 'xtick.labelsize': 14, 'ytick.labelsize': 14,
                     'legend.fontsize': 13, 'font.family': 'DejaVu Sans', 'axes.spines.top': False, 'axes.spines.right': False,
                     'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.facecolor': 'white',
                     'svg.hashsalt': 'gmaps-release'})   # fixed SVG ids: a re-render is byte-identical


def held_out():
    """{(system, set): row} for the Spark offline rows and the gym eval run rows (system 'gym:<model>')."""
    rows = {}
    for r in SB.load_offline(REPO / 'gym/rollouts'):
        rows[(r['system'], r['set'])] = r
    for r in SB.load_gym_by_set(REPO / 'gym/rollouts'):
        rows[('gym:' + r['system'].split(' (')[0], r['set'])] = r
    brev = {}
    for r in SB.load_offline(REPO / 'eval/results/brev'):
        brev[(r['system'], r['set'].replace('_brev', '').replace('_endpoint', ''))] = r
    return rows, brev


def n_of(rows, s):
    return next(r['n'] for (sy, st), r in rows.items() if st == s)


def fig(h, title, subtitle=None):
    f = plt.figure(figsize=(10, h), dpi=200)
    f.text(0.02, 0.985, title, fontsize=18, fontweight='bold', va='top'); TEXTS.append(title)
    if subtitle:
        f.text(0.02, 0.985 - 0.42 / h, subtitle, fontsize=14, color='#374151', va='top'); TEXTS.append(subtitle)
    f.text(0.02, 0.012, FOOTER, fontsize=12, color=GREY, va='bottom'); TEXTS.append(FOOTER)
    return f


def label(ax, chart, x, y, value, decimals, key, text=None, **kw):
    t = text if text is not None else f'{value:.{decimals}f}'
    ax.text(x, y, t, **({'ha': 'center', 'va': 'bottom', 'fontsize': 13} | kw))
    LABELS.append((chart, t, value, decimals, key))


def save(f, out, stem):
    for ext in ('png', 'svg'):
        f.savefig(out / f'{stem}.{ext}', dpi=200, metadata={'Date': None} if ext == 'svg' else None)   # no build timestamp in the file
    plt.close(f)
    return stem


def grouped(chart, out, stem, metric, title, ylabel, ceiling=None):
    rows, _ = held_out()
    f = fig(6.4, title, 'held-out, same verifier · bars = share of transmissions')
    ax = f.add_axes([0.09, 0.2, 0.89, 0.58])
    systems, w = ['base', 'grammar', 'gmaps-parser'], 0.26
    for i, (s, nice) in enumerate(SETS):
        for j, sy in enumerate(systems):
            x = i + (j - 1) * w
            r = rows.get((sy, s))
            if r is None:
                ax.text(x, 1.5, 'not run\non this set', ha='center', va='bottom', fontsize=10, color=GREY)
                continue
            v = 100 * r[metric]
            ax.bar(x, v, w * 0.92, color=COLOR[sy], label=NAME[sy] if i == 0 else None)
            label(ax, chart, x, v + 1, round(v, 1), 1, ('main', sy, s, metric))
        if ceiling and (ceiling, s) in rows:
            c = 100 * rows[(ceiling, s)][metric]
            ax.plot([i - 1.6 * w, i + 1.6 * w], [c, c], ls='--', color=BLACK, lw=2)
            label(ax, chart, i - 1.6 * w, c + 1.2, round(c, 1), 1, ('main', ceiling, s, metric),
                  text=f'ceiling: perfect answer,\ngrounding rule {c:.1f}', fontsize=11, ha='left')
    ax.set_xticks(range(len(SETS)), [f'{nice}\nn={n_of(rows, s)}' for s, nice in SETS]); TEXTS.extend(t.get_text() for t in ax.get_xticklabels())
    ax.set_ylim(0, 100); ax.set_ylabel(ylabel); TEXTS.append(ylabel)
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.02), ncol=2, frameon=False, fontsize=12)
    return save(f, out, stem)


def chart_a(out):
    return grouped('a', out, 'record_exact', 'record_exact', 'Record exact: all five fields right', 'record exact, %')


def chart_b(out):
    return grouped('b', out, 'correct_and_accepted', 'both', 'Correct AND accepted by the grounding rule', 'correct and accepted, %', ceiling='gold')


def chart_c(out):
    rows, _ = held_out()
    st = PS.paired(PS.load(REPO / 'gym/rollouts/offline_heldout4_asr_endpoint_base.json', 'reward')[1],
                   PS.load(REPO / 'gym/rollouts/offline_heldout4_asr_endpoint_gmaps-parser.json', 'reward')[1])
    items = [('record exact', 'heldout4_asr', 'record_exact'), ('correct and accepted', 'heldout4_asr', 'both'),
             ('record exact, with\nvocabulary prompt', 'heldout4_asr_prompt', 'record_exact')]
    f = fig(6.4, 'Untuned base vs gmaps-parser on Whisper text', 'held-out, same verifier · same 4B base, same server, n=138 each')
    ax = f.add_axes([0.3, 0.24, 0.66, 0.54])
    h = 0.36
    for i, (nice, s, m) in enumerate(items):
        y = len(items) - 1 - i
        for k, sy in enumerate(('base', 'gmaps-parser')):
            v = 100 * rows[(sy, s)][m]
            yy = y + (0.5 - k) * h
            ax.barh(yy, v, h * 0.9, color=COLOR[sy], label=NAME[sy] if i == 0 else None)
            label(ax, 'c', v + 1, yy, round(v, 1), 1, ('main', sy, s, m), ha='left', va='center')
    ax.set_yticks(range(len(items)), [f'{nice}\nn={n_of(rows, s)}' for nice, s, m in reversed(items)]); TEXTS.extend(t.get_text() for t in ax.get_yticklabels())
    ax.set_xlim(0, 100); ax.set_xlabel('%'); ax.legend(loc='lower left', bbox_to_anchor=(-0.35, 1.02), ncol=2, frameon=False, fontsize=12)
    gain, (lo, hi), p = round(st['gain_pts'], 1), st['gain_ci_pts'], st['mcnemar_p']
    call = f'record exact: {gain:+.1f} points, paired,\n95 % CI [{lo:+.1f}, {hi:+.1f}], McNemar p = {p:.2g}'
    f.text(0.3, 0.06, call, fontsize=13, fontweight='bold', color=BLUE); TEXTS.append(call)
    LABELS.extend([('c', f'{gain:+.1f}', gain, 1, ('paired', 'gain')), ('c', f'{lo:+.1f}', round(lo, 1), 1, ('paired', 'lo')),
                   ('c', f'{hi:+.1f}', round(hi, 1), 1, ('paired', 'hi')), ('c', f'{p:.2g}', float(f'{p:.2g}'), None, ('paired', 'p'))])
    return save(f, out, 'base_vs_adapter')


def chart_d(out):
    rows, _ = held_out()
    pairs = [(sy, s) for sy in ('base', 'gmaps-parser') for s, _ in SETS]
    total = {sy: sum(rows[('gym:' + sy, s)]['n'] for s, _ in SETS) for sy in ('base', 'gmaps-parser')}
    assert total['base'] == total['gmaps-parser'], total
    f = fig(6.6, 'Record exact: NeMo Gym orchestrated vs offline runner',
            f'held-out, same verifier · {total["base"]} rollouts per model, 0 failures · 5 of 6 pairs identical')
    ax = f.add_axes([0.08, 0.24, 0.9, 0.52])
    w = 0.38
    short = {'heldout4_asr': 'Whisper', 'heldout4_asr_prompt': 'Whisper\n+ prompt', 'heldout5_script': 'blind\nscripted'}
    for i, (sy, s) in enumerate(pairs):
        for k, (src, hatch) in enumerate(((sy, None), ('gym:' + sy, '//'))):
            v = 100 * rows[(src, s)]['record_exact']
            x = i + (k - 0.5) * w
            ax.bar(x, v, w * 0.92, color=COLOR[sy], hatch=hatch, edgecolor='white', lw=0)
            label(ax, 'd', x, v + 1, round(v, 1), 1, ('main', src, s, 'record_exact'), fontsize=11)
    ax.set_xticks(range(len(pairs)), [f'{"base" if sy == "base" else "ours"} · {short[s]}\nn={n_of(rows, s)}' for sy, s in pairs], fontsize=12)
    TEXTS.extend(t.get_text() for t in ax.get_xticklabels())
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=GREY, label=NAME['base']), Patch(color=BLUE, label=NAME['gmaps-parser']),
                       Patch(facecolor='white', edgecolor=BLACK, label='solid = offline runner'),
                       Patch(facecolor='white', edgecolor=BLACK, hatch='//', label='hatched = gym eval run')],
              loc='lower left', bbox_to_anchor=(0, 1.02), ncol=2, frameon=False, fontsize=12)
    ax.set_ylim(0, 100); ax.set_ylabel('record exact, %')
    LABELS.append(('d', str(total['base']), total['base'], 0, ('gymtotal',)))
    return save(f, out, 'gym_vs_offline')


def chart_e(out):
    _, brev = held_out()
    f = fig(6.6, 'More data scored lower: record exact', 'held-out, same verifier · both adapters scored on one machine (L40S), same task files')
    ax = f.add_axes([0.09, 0.2, 0.89, 0.54])
    w = 0.36
    sysn = {'gmaps-parser': 'submitted adapter: 3,000 pairs, 1 epoch', 'gmaps-parser-full': 'full-data adapter (not submitted): 7,065 pairs, 2 epochs'}
    for i, (s, nice) in enumerate(SETS):
        vals = {}
        for k, sy in enumerate(('gmaps-parser', 'gmaps-parser-full')):
            v = 100 * brev[(sy, s)]['record_exact']; vals[sy] = round(v, 1)
            x = i + (k - 0.5) * w
            ax.bar(x, v, w * 0.92, color=COLOR[sy], label=sysn[sy] if i == 0 else None)
            label(ax, 'e', x, v + 1, round(v, 1), 1, ('brev', sy, s, 'record_exact'))
        d = round(vals['gmaps-parser-full'] - vals['gmaps-parser'], 1)
        label(ax, 'e', i + 0.5 * w, 100 * brev[('gmaps-parser-full', s)]['record_exact'] - 3, d, 1, ('brevdelta', s),
              text=f'{d:+.1f}\npoints', fontsize=13, color='white', fontweight='bold', va='top')
    ax.set_xticks(range(len(SETS)), [f'{nice}\nn={brev[("gmaps-parser", s)]["n"]}' for s, nice in SETS]); TEXTS.extend(t.get_text() for t in ax.get_xticklabels())
    ax.set_ylim(0, 100); ax.set_ylabel('record exact, %')
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.02), ncol=1, frameon=False, fontsize=12)
    return save(f, out, 'more_data_scored_lower')


def chart_f(out):
    rows, _ = held_out()
    s = 'heldout4_asr'
    items = [('grammar', 'lat_med', 'median'), ('grammar', 'lat_p95', 'p95'), ('gmaps-parser', 'lat_med', 'median'), ('gmaps-parser', 'lat_p95', 'p95')]
    f = fig(5.6, 'Parser latency per transmission', f'held-out Whisper text, n={rows[("grammar", s)]["n"]}, same verifier · DGX Spark, vLLM v0.27.1')
    ax = f.add_axes([0.3, 0.2, 0.62, 0.6])
    for i, (sy, k, nice) in enumerate(items):
        v = rows[(sy, s)][k]
        y = len(items) - 1 - i
        ax.barh(y, v, 0.7, color=COLOR[sy], hatch='//' if nice == 'p95' else None, edgecolor='white', lw=0)
        dec = 2 if v < 0.1 else 1
        label(ax, 'f', v * 1.12, y, round(v, dec), dec, ('main', sy, s, k), text=f'{v:.{dec}f} s', ha='left', va='center')
    ax.set_yticks(range(len(items)), [f'{"grammar" if sy == "grammar" else "gmaps-parser"} · {nice}' for sy, k, nice in reversed(items)])
    TEXTS.extend(t.get_text() for t in ax.get_yticklabels())
    ax.set_xscale('log'); ax.set_xlim(0.01, 10); ax.set_xlabel('seconds per transmission (log scale)')
    return save(f, out, 'latency')


def demo_counts():
    out = {}
    for ep, nice in (('SIM_E3_STOP_NOT_OBSERVED', 'SIM_E3 (fictional)'), ('SIM_E1_CROSS_DURING_ROLLOUT', 'SIM_E1 (fictional)')):   # fictional scenarios only
        pc = json.loads((REPO / f'ui_scaffold/samples/parsers_{ep}.json').read_text())
        sample = json.loads((REPO / f'ui_scaffold/samples/{ep}.json').read_text())
        tr = sample.get('transcript') or [   # a fixture: the page's normalizeFixture() (timeline event -> kind, callsigns)
            {'t': x['t'], 'kind': (x.get('event') or {}).get('kind') or x.get('kind') or 'OTHER',
             'callsigns': [c.lstrip('?') for c in (x.get('event') or {}).get('actor_candidates') or []]} for x in sample['timeline']]

        def ok(row, sy, typed):
            r = row.get(sy) or {}
            if not r.get('accepted'):
                return False
            if not typed:
                return True
            line = next((x for x in tr if x['t'] == row['t']), {}); cs = (line.get('callsigns') or [None])[0]; fl = r.get('fields') or {}
            return not (cs and fl.get('callsign') and fl['callsign'] != cs) and not (line.get('kind') and fl.get('instruction') and fl['instruction'] != line['kind'])
        rows = pc['rows']
        out[ep] = {'nice': nice, 'n': len(rows), **{f'{m}:{sy}': sum(ok(r, sy, m == 'typed') for r in rows)
                                                   for m in ('accepted', 'typed') for sy in ('grammar', 'base', 'gmaps-parser')}}
    return out


def chart_g(out):
    dc = demo_counts()
    f = fig(9.4, "Vehicle callsigns are outside the adapter's scope", 'scripted demo lines with vehicle callsigns (fictional SIM-1) · same runtime check')
    w = 0.26
    for p, (m, nice) in enumerate((('accepted', 'accepted by the runtime'), ('typed', 'typed right (accepted, callsign + instruction match the ledger line)'))):
        ax = f.add_axes([0.09, 0.53 - p * 0.42, 0.89, 0.25])
        ax.set_title(nice, loc='left', fontsize=14); TEXTS.append(nice)
        for i, (ep, d) in enumerate(dc.items()):
            for j, sy in enumerate(('grammar', 'base', 'gmaps-parser')):
                k = d[f'{m}:{sy}']; v = 100 * k / d['n']; x = i + (j - 1) * w
                ax.bar(x, v, w * 0.92, color=COLOR[sy], label=NAME[sy] if (i == 0 and p == 0) else None)
                label(ax, 'g', x, v + 2, k, 0, ('demo', ep, m, sy), text=f'{k}/{d["n"]}', fontsize=12)
        ax.set_xticks(range(len(dc)), [f'{d["nice"]}\nn={d["n"]}' for d in dc.values()]); TEXTS.extend(t.get_text() for t in ax.get_xticklabels())
        ax.set_ylim(0, 115); ax.set_yticks([0, 25, 50, 75, 100]); ax.set_ylabel('% of lines')
        if p == 0:
            ax.legend(loc='lower left', bbox_to_anchor=(0, 1.13), ncol=2, frameon=False, fontsize=12)
    return save(f, out, 'demo_lines_vehicle_callsigns')


def chart_dataset(out):
    m = json.loads((REPO / 'eval/synthetic/train_ft/MANIFEST.json').read_text())
    ic = sorted(m['instruction_counts'].items(), key=lambda kv: kv[1])
    per = sorted({c['transmissions'] for c in m['counts'].values()})
    by_ap = f'{len(m["counts"])} airports\' identifier sets, {per[0]} each' if len(per) == 1 else f'{len(m["counts"])} airports\' identifier sets'
    ov = m['overlap_with_heldout3_heldout4_after_exclusion']
    f = plt.figure(figsize=(10, 6.4), dpi=200)
    f.text(0.02, 0.985, f'Training data: {m["transmissions_total"]:,} scripted transmissions by instruction', fontsize=18, fontweight='bold', va='top')
    f.text(0.02, 0.92, f'synthetic FAA JO 7110.65 phraseology · {by_ap}\nheld-out overlap: exact {ov["exact"]}, normalized {ov["normalized"]}',
           fontsize=13, color='#374151', va='top')
    f.text(0.02, 0.012, 'SIMULATED data. Fictional callsigns and traffic.', fontsize=12, color=GREY, va='bottom')
    ax = f.add_axes([0.3, 0.12, 0.62, 0.68])
    ax.barh(range(len(ic)), [c for _, c in ic], 0.7, color=BLUE)
    for i, (_, c) in enumerate(ic):
        ax.text(c + 8, i, str(c), va='center', fontsize=13)
    ax.set_yticks(range(len(ic)), [k for k, _ in ic]); ax.set_xlabel('transmissions'); ax.set_xlim(0, max(c for _, c in ic) * 1.12)
    return save(f, out, 'dataset_composition')


CHARTS = {'a': chart_a, 'b': chart_b, 'c': chart_c, 'd': chart_d, 'e': chart_e, 'f': chart_f, 'g': chart_g}


def scoreboard_md():
    """Independent read of docs/SCOREBOARD.md (the drawing reads the JSON files): {(table, system, set): {column: value}}."""
    out, section, hdr = {}, 'main', None
    for l in (REPO / 'docs/SCOREBOARD.md').read_text().splitlines():
        if l.startswith('## '):
            section = 'brev' if l.startswith('## Brev') else 'demo' if l.startswith('## Demo') else 'other'
        if not l.startswith('|'):
            hdr = None; continue
        cells = [x.strip().strip('*') for x in l.strip().strip('|').split('|')]
        if hdr is None:
            hdr = cells; continue
        if l.startswith('|---'):
            continue
        c = dict(zip(hdr, cells))
        if section == 'demo':
            out[('demo', c['scenario'])] = c
        elif section in ('main', 'brev') and 'set' in c:
            out[(section, c['system'], c['set'])] = c
    return out


def verify():
    md = scoreboard_md()
    ps = (REPO / 'docs/PAIRED_STATS.md').read_text()
    m = re.search(r'\| gmaps-parser \| 138 \| 0\.652 [^|]+\| ([+-][\d.]+) \[([+-][\d.]+), ([+-][\d.]+)\] \| \d+ \| \d+ \| ([\d.e-]+) \|', ps)
    claims = next(l for l in (REPO / 'docs/CLAIMS_LEDGER.md').read_text().splitlines() if l.startswith('| 14 |'))
    typed = {'SIM_E3_STOP_NOT_OBSERVED': re.search(r'SIM_E3 \(n = \d+\): [^.]*?typed right (\d+) / (\d+) / (\d+)', claims),
             'SIM_E1_CROSS_DURING_ROLLOUT': re.search(r'SIM_E1 \(n = \d+\): [^.]*?typed right (\d+) / (\d+) / (\d+)', claims)}
    brevname = {'gmaps-parser': 'gmaps-parser (run1 adapter, Brev)', 'gmaps-parser-full': 'gmaps-parser-full (7,065 rows × 2 ep, Brev)'}
    col = {'record_exact': 'record exact', 'both': 'correct AND accepted'}
    bad = {k: [] for k in CHARTS}
    seen = {k: 0 for k in CHARTS}

    def want(key):
        kind = key[0]
        if kind == 'main':
            _, sy, s, metric = key
            gym = sy.startswith('gym:')
            row = md[('main', sy[4:] + ' (gym eval run)' if gym else sy, s)]
            if metric in ('lat_med', 'lat_p95'):
                return float(row['latency med / p95 s'].split(' / ')[0 if metric == 'lat_med' else 1])
            return 100 * float(row[col[metric]])
        if kind == 'brev':
            return 100 * float(md[('brev', brevname[key[1]], key[2])]['record exact'])
        if kind == 'brevdelta':
            g = lambda sy: round(100 * float(md[('brev', brevname[sy], key[1])]['record exact']), 1)
            return round(g('gmaps-parser-full') - g('gmaps-parser'), 1)
        if kind == 'paired':
            return {'gain': float(m.group(1)), 'lo': float(m.group(2)), 'hi': float(m.group(3)), 'p': float(m.group(4))}[key[1]]
        if kind == 'gymtotal':
            return sum(int(md[('main', 'base (gym eval run)', s)]['n']) for s, _ in SETS)
        if kind == 'demo':
            _, ep, metric, sy = key
            if metric == 'accepted':
                return int(md[('demo', ep)][{'grammar': 'grammar accepted', 'base': 'base accepted', 'gmaps-parser': 'gmaps-parser accepted'}[sy]].split('/')[0])
            return int(typed[ep].group({'grammar': 1, 'base': 2, 'gmaps-parser': 3}[sy]))
        raise KeyError(key)

    for chart, text, value, dec, key in LABELS:
        seen[chart] += 1
        try:
            w = want(key)
        except Exception as e:
            bad[chart].append(f'{text}: no scoreboard value for {key} ({e})'); continue
        ok = (abs(value - w) / max(abs(w), 1e-30) < 0.01) if dec is None else (round(w, dec) == round(value, dec) and abs(round(w, 1) - round(value, 1)) < 0.05 + 1e-9)
        if not ok:
            bad[chart].append(f'{text}: drawn {value}, scoreboard {w} ({key})')
    for t in TEXTS + [t for _, t, *_ in LABELS]:
        if DATE_RX.search(t):
            bad['a'].append(f'date in chart text: {t!r}')
    good = [k for k in CHARTS if seen[k] and not bad[k]]
    for k in CHARTS:
        print(f'  {k}: {seen[k]} labels checked' + ('' if not bad[k] else ' · FAIL: ' + '; '.join(bad[k])))
    print(f'CHARTS {"OK" if len(good) == len(CHARTS) else "FAIL"} {len(good)}/{len(CHARTS)}')
    return len(good) == len(CHARTS)


def chart_open_agent(compare_json, out):
    """Open Agent Hackathon 2026: record exact for grammar / untuned 4B / fine-tuned 4B / Nemotron via Nightshift on the SAME tasks,
    every value read from eval/results/open_agent_2026/nemotron_vs_parser_<set>.json (written by eval/run_provider_compare.py)."""
    d = json.loads(Path(compare_json).read_text())
    rows = [r for r in d['comparison'] if not r.get('missing')]
    nem = rows[-1]
    if not nem.get('n'):
        sys.exit('no Nemotron rows scored: nothing to chart')
    colors = [GREEN, GREY, BLUE, '#76b900']
    short = ['grammar\n(no model)', 'untuned 4B\n(local)', 'fine-tuned 4B\ngmaps-parser (local)', 'Nemotron 30B\nvia Nightshift']
    n = nem['n']
    part = f"first {n} of {d['tasks_in_set']} (deadline)" if d.get('stopped') else f'all {n}'
    f = fig(6.4, f"Record exact on {d['set']}: {part} transmissions",
            f"same tasks, prompt and verifier for every bar · Nemotron: {', '.join(nem.get('model_reported') or ['model id not reported'])}")
    ax = f.add_axes([0.08, 0.2, 0.9, 0.58])
    for i, r in enumerate(rows):
        v = r['record_exact']
        ax.bar(i, v, color=colors[i % len(colors)], width=0.62)
        label(ax, 'open_agent', i, v + 0.01, v, 3, (r['system'], d['set']))
    ax.set_xticks(range(len(rows)), short[:len(rows)]); TEXTS.extend(short)
    ax.set_ylim(0, 1.05); ax.set_ylabel('record exact (all five fields)')
    f.text(0.98, 0.012, f"n = {n} · eval/results/open_agent_2026/{Path(compare_json).name}", fontsize=11, color=GREY, ha='right', va='bottom')
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    return save(f, out, 'nemotron_vs_parser')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='directory for the model-card charts (<bundle>/assets)')
    ap.add_argument('--open-agent-compare', help='Open Agent 2026: draw ONLY the Nemotron-vs-parser chart from this JSON into --out')
    ap.add_argument('--dataset-out', help='directory for the dataset-card chart (<bundle>/dataset/assets)')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args(argv)
    if not a.out:
        ap.error('--out is required')
    if a.open_agent_compare:
        print(f'open_agent: {a.out}/{chart_open_agent(a.open_agent_compare, Path(a.out).expanduser())}.png'); return
    out = Path(a.out).expanduser(); out.mkdir(parents=True, exist_ok=True)
    for k, fn in CHARTS.items():
        print(f'{k}: {out}/{fn(out)}.png')
    if a.dataset_out:
        d = Path(a.dataset_out).expanduser(); d.mkdir(parents=True, exist_ok=True)
        print(f'dataset: {d}/{chart_dataset(d)}.png')
    if a.verify and not verify():
        sys.exit(1)


if __name__ == '__main__':
    main()
