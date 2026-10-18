"""One-command comparison table for the pitch. Reads the offline-runner results in gym/rollouts/
(and, if present, eval/results/parser_baselines*.json and train run report.json files) and writes a single self-contained
HTML page plus a Markdown table: one row per system, columns = record exact match, per-field exact match, JSON-valid rate,
runtime-accepted rate (groundable), correct-AND-accepted, median / p95 latency, n, data set. No external assets, no JS.

  python3 tools/scoreboard.py                          # -> docs/scoreboard.html + docs/SCOREBOARD.md
  python3 tools/scoreboard.py --rollouts gym/rollouts --out docs/scoreboard.html

Every number on the page comes from a file this script names in the "source" column. SIMULATED data; labelled as such.
"""
import argparse, html, json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIELDS = ('speaker_role', 'callsign', 'runway', 'instruction', 'taxiways')


def load_offline(d):
    rows = []
    for p in sorted(Path(d).glob('offline_*.json')):
        try:
            s = json.loads(p.read_text())['summary']
        except Exception:
            continue
        rows.append({'system': s.get('model') or s.get('policy'), 'policy': s['policy'], 'set': Path(s['input']).stem, 'n': s['n'],
                     'record_exact': s['record_exact'], 'fields': s['field_exact'], 'json_valid': s['json_valid'], 'groundable': s['groundable'],
                     'both': s.get('record_exact_and_groundable'), 'lat_med': s['latency_s']['median'], 'lat_p95': s['latency_s']['p95'],
                     'source': str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)})
    return rows


def load_reports():
    out = []
    for p in sorted(REPO.glob('**/report.json')):
        if '/outputs/' in str(p) or '/.git/' in str(p):
            continue
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        if 'base_model_dir' in r or 'base_model' in r:
            out.append({'run': p.parent.name, 'base': r.get('base_model_dir') or r.get('base_model'), 'rows': r.get('rows'), 'steps': r.get('steps'),
                        'first_loss': r.get('first_loss'), 'last_loss': r.get('last_loss'), 'runtime_s': r.get('train_runtime_s'),
                        'peak_gib': r.get('peak_gpu_mem_gib'), 'device': r.get('device') or r.get('gpu'), 'source': str(p)})
    return out


def load_gym_by_set(d):
    """Rows from gym/aggregate_by_set.py (orchestrated `gym eval run`); dryrun_* files (mock policies) are skipped."""
    rows = []
    for p in sorted(Path(d).glob('*_by_set.json')):
        if p.name.startswith('dryrun_'):
            continue
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        for key, s in r.get('sets', {}).items():
            rows.append({'system': f"{r.get('model')} (gym eval run)", 'policy': 'gym', 'set': key, 'n': s['n'], 'record_exact': s['record_exact'],
                         'fields': s['field_exact'], 'json_valid': s['json_valid'], 'groundable': s['groundable'], 'both': s.get('record_exact_and_groundable'),
                         'lat_med': None, 'lat_p95': None, 'source': str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)})
    return rows


def load_toolbench(*dirs):
    rows = []
    files = {}   # same run in both places (copied to eval/results/toolbench): keep the tracked copy, the first dir wins
    for d in dirs:
        for p in Path(d).glob('toolbench_*.json'):
            files.setdefault(p.name, p)
    for p in sorted(files.values(), key=lambda p: p.name):
        try:
            r = json.loads(p.read_text())
        except Exception:
            continue
        label = r['model'] if r.get('procedure') else f"{r['model']} [v1]"   # runs before the --procedure flag used the v1 prompt
        if r.get('episode') and r['episode'] != 'SIM_E1_CROSS_DURING_ROLLOUT':
            label += f" · {r['episode']}"
        for sc, s in r.get('summary', {}).items():
            rows.append({'model': label, 'scenario': sc, **s, 'source': str(p.relative_to(REPO)) if p.is_relative_to(REPO) else str(p)})
    return rows


TB_COLS = [('runs', 'runs'), ('pass_rate', 'pass'), ('ingest_recall_mean', 'ingest recall'), ('alerts_exact_rate', 'alerts exact'), ('invalid_call_rate', 'invalid calls'),
           ('recoveries', 'recoveries'), ('faults_injected', 'faults'), ('refused_retries', 'refused retries'), ('refused_reposts', 'refused re-posts'),
           ('model_calls_per_turn', 'model calls / turn'), ('model_latency_median_s', 'model latency med s'), ('time_to_alert_median_s', 'time to alert s')]


def fmt(x):
    return '—' if x is None else (f'{x:.3f}' if isinstance(x, float) else str(x))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rollouts', default=str(REPO / 'gym/rollouts'))
    ap.add_argument('--out', default=str(REPO / 'docs/scoreboard.html'))
    a = ap.parse_args()
    rows = load_offline(a.rollouts) + load_gym_by_set(a.rollouts)
    reports = load_reports()
    tb = load_toolbench(REPO / 'eval/results/toolbench', a.rollouts)
    rows.sort(key=lambda r: (r['set'], -(r['record_exact'] or 0)))
    md = ['# Scoreboard (SIMULATED held-out sets; generated by tools/scoreboard.py)', '',
          '| system | set | n | record exact | speaker | callsign | runway | instruction | taxiways | JSON valid | accepted by runtime | correct AND accepted | latency med / p95 s | source |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        f = r['fields']
        md.append(f"| {r['system']} | {r['set']} | {r['n']} | **{fmt(r['record_exact'])}** | " + ' | '.join(fmt(f.get(k)) for k in FIELDS) +
                  f" | {fmt(r['json_valid'])} | {fmt(r['groundable'])} | {fmt(r['both'])} | {fmt(r['lat_med'])} / {fmt(r['lat_p95'])} | `{r['source']}` |")
    if reports:
        md += ['', '## Training runs', '', '| run | base | rows | steps | first loss | last loss | runtime s | peak GiB | device | source |', '|---|---|---|---|---|---|---|---|---|---|']
        for r in reports:
            md.append(f"| {r['run']} | {r['base']} | {r['rows']} | {r['steps']} | {fmt(r['first_loss'])} | {fmt(r['last_loss'])} | {fmt(r['runtime_s'])} | {fmt(r['peak_gib'])} | {r['device']} | `{r['source']}` |")
    if tb:
        md += ['', '## Tool-calling reliability (venue/tool_loop_bench.py, SIMULATED E1)', '',
               '| model | scenario | ' + ' | '.join(h for _, h in TB_COLS) + ' | source |', '|---|---|' + '---|' * len(TB_COLS) + '---|']
        for r in tb:
            md.append(f"| {r['model']} | {r['scenario']} | " + ' | '.join(fmt(r.get(k)) for k, _ in TB_COLS) + f" | `{r['source']}` |")
        md += ['', 'pass = every typed event ingested once, exactly the reference CONFLICT warnings posted once, a look-ahead at t in every turn, a summary every turn, '
               '"unavailable" said whenever the look-ahead answered so, no refused retries or re-posts, recovery after every injected fault. '
               'Scenarios: positive (feed on), no_feed, transient (first ledger_ingest fails), stale (first look-ahead forced unavailable).']
    md += ['', 'record exact = all five fields match the scripted-scenario gold. accepted by runtime = the answer also passes venue/llm_fallback.py grounding on the transmission text. '
           'Fictional traffic, synthetic FAA JO 7110.65 phraseology; decision support only, the controller decides.']
    # hand-attached sections (e.g. "Brev L40S rows", "Demo scenario lines") are carried over verbatim, in their order, after the generated ones
    out = REPO / 'docs/SCOREBOARD.md'
    generated = {l[3:] for l in md if l.startswith('## ')}
    if out.exists():
        for sec in out.read_text().split('\n## ')[1:]:
            if sec.split('\n', 1)[0] not in generated:
                md += ['', '## ' + sec.rstrip('\n')]
    out.write_text('\n'.join(md) + '\n')
    css = ('body{font:14px/1.4 system-ui,sans-serif;margin:24px;color:#111}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:6px 8px;text-align:right;white-space:nowrap}'
           'th:first-child,td:first-child,td:nth-child(2),th:nth-child(2){text-align:left}th{background:#f3f3f3;position:sticky;top:0}td.b{font-weight:700}caption{text-align:left;font-weight:600;margin:8px 0}'
           'small{color:#555}.set{background:#fafafa}')
    h = ['<!doctype html><meta charset="utf-8"><title>GMAPS parser scoreboard</title><style>' + css + '</style>',
         '<h1>GMAPS parser: specialization vs general model</h1><p><small>SIMULATED held-out sets (synthetic FAA JO 7110.65 phraseology, fictional traffic). '
         'record exact = all five fields match the gold; accepted = also passes the runtime grounding rule. Generated by tools/scoreboard.py from the files in the source column.</small></p>',
         '<table><caption>Held-out results</caption><tr><th>system</th><th>set</th><th>n</th><th>record exact</th>' + ''.join(f'<th>{k}</th>' for k in FIELDS) +
         '<th>JSON valid</th><th>accepted by runtime</th><th>correct AND accepted</th><th>latency med / p95 s</th><th>source</th></tr>']
    for r in rows:
        f = r['fields']
        h.append(f"<tr><td>{html.escape(str(r['system']))}</td><td class=set>{html.escape(r['set'])}</td><td>{r['n']}</td><td class=b>{fmt(r['record_exact'])}</td>" +
                 ''.join(f'<td>{fmt(f.get(k))}</td>' for k in FIELDS) +
                 f"<td>{fmt(r['json_valid'])}</td><td>{fmt(r['groundable'])}</td><td>{fmt(r['both'])}</td><td>{fmt(r['lat_med'])} / {fmt(r['lat_p95'])}</td><td><small>{html.escape(r['source'])}</small></td></tr>")
    h.append('</table>')
    if reports:
        h.append('<table><caption>Training runs</caption><tr><th>run</th><th>base</th><th>rows</th><th>steps</th><th>first loss</th><th>last loss</th><th>runtime s</th><th>peak GiB</th><th>device</th></tr>')
        for r in reports:
            h.append(f"<tr><td>{html.escape(r['run'])}</td><td>{html.escape(str(r['base']))}</td><td>{r['rows']}</td><td>{r['steps']}</td><td>{fmt(r['first_loss'])}</td><td>{fmt(r['last_loss'])}</td><td>{fmt(r['runtime_s'])}</td><td>{fmt(r['peak_gib'])}</td><td>{html.escape(str(r['device']))}</td></tr>")
        h.append('</table>')
    if tb:
        h.append('<table><caption>Tool-calling reliability (venue/tool_loop_bench.py, SIMULATED E1)</caption><tr><th>model</th><th>scenario</th>' + ''.join(f'<th>{hh}</th>' for _, hh in TB_COLS) + '</tr>')
        for r in tb:
            h.append(f"<tr><td>{html.escape(str(r['model']))}</td><td class=set>{html.escape(r['scenario'])}</td>" + ''.join(f'<td>{fmt(r.get(k))}</td>' for k, _ in TB_COLS) + '</tr>')
        h.append('</table><p><small>pass = procedure followed end to end: events ingested once, the reference CONFLICT posted once, look-ahead every turn, summary every turn, '
                 '"unavailable" reported when answered, no refused retries or re-posts, recovery after injected faults.</small></p>')
    h.append('<p><small>Decision support only; the controller decides. "Prediction unavailable" is not "no conflict".</small></p>')
    Path(a.out).write_text('\n'.join(h) + '\n')
    print(f'{len(rows)} result rows, {len(tb)} tool-bench rows, {len(reports)} training runs -> {a.out} and docs/SCOREBOARD.md')


if __name__ == '__main__':
    main()
