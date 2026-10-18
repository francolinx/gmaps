"""Regression harness for the airport-map pipeline: rebuild every airport and compare it with its stored baseline.
Run it after every pipeline change. Stdlib only (the build stages use .venv where they need it).

  python3 tools/airport_map/regress.py                    # rebuild all airports (tools/build_airport.py) and compare
  python3 tools/airport_map/regress.py KHWD KLGA          # only these
  python3 tools/airport_map/regress.py --no-build         # compare the current outputs without rebuilding
  python3 tools/airport_map/regress.py --update-unsigned  # accept the new results of UNSIGNED airports as their baseline

Compared: object and label class counts, overlay covered/extra %, georef residual per runway end (and helipads),
ILS hold-line count, and every checklist status.
  - A SIGNED airport (review.json has a sign_off) must match its baseline exactly; any change fails (exit 1), and its
    baseline can never be updated here. A signed result changes only through a new review and sign-off by Franco.
  - An UNSIGNED airport's changes are reported (exit 0); --update-unsigned stores them as the new baseline.
  - A missing baseline is created from the current results (reported as 'new').
Rebuilding rewrites timestamps. A tracked file that was clean before the build and differs only in timestamps is
restored afterwards, so a no-change run leaves no diff (signed review.json files are never left with churn).
Baselines: data/airports/<icao>/review/regress_baseline.json. Last report: ~/.cache/gmaps/regress/last.json.
"""
import argparse, collections, json, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AIRPORTS = REPO / 'data/airports'
REPORT = Path.home() / '.cache/gmaps/regress/last.json'
TS_KEYS = {'generated_at', 'retrieved_on', 'checked_at', 'now_utc', 'retrieved_at', 'requested_at', 'days_left', 'read_at', 'built_at', 'fetched_at', 'saved_at'}


def airports():
    return sorted(p.name.upper() for p in AIRPORTS.iterdir() if p.is_dir() and (p / 'review.json').exists())


def signed(icao):
    return bool(json.loads((AIRPORTS / icao.lower() / 'review.json').read_text()).get('sign_off'))


def metrics(icao):
    b = AIRPORTS / icao.lower()
    raw = json.loads((b / 'build/layers.raw.json').read_text())
    rv = json.loads((b / 'review.json').read_text())
    ov = json.loads((b / 'review/overlay_check.json').read_text())
    g = json.loads((b / 'georef.json').read_text())
    items = {i['id']: i for i in rv['items']}
    m = {'objects': dict(sorted(collections.Counter(o['class'] for o in raw['objects']).items())),
         'labels': dict(sorted(collections.Counter(l['class'] for l in raw['labels']).items())),
         'overlay': {'covered_pct': ov['covered_pct'], 'extra_pct': ov['extra_pct']},
         'georef_m': {e['id']: e['residual_m'] for e in g.get('runway_ends', [])},
         'hold_lines': (items.get('ils_hold_lines', {}).get('result') or {}).get('hold_lines'),
         'statuses': {k: v['status'] for k, v in items.items()}}
    if rv.get('draft'):   # recorded only for a draft map, so every other baseline is unchanged
        m['draft'] = rv['draft']['status']
    for k, v in g.items():
        if k.startswith('helipad_') and isinstance(v, dict) and 'residual_m' in v:
            m['georef_m'][k[len('helipad_'):]] = v['residual_m']
    return m


def diff(a, b, path=''):
    out = []
    for k in sorted(set(a) | set(b), key=str):
        x, y, p = a.get(k), b.get(k), f'{path}.{k}' if path else str(k)
        if isinstance(x, dict) and isinstance(y, dict):
            out += diff(x, y, p)
        elif x != y:
            out.append({'key': p, 'baseline': x, 'now': y})
    return out


def strip_ts(o):
    if isinstance(o, dict):
        return {k: strip_ts(v) for k, v in o.items() if k not in TS_KEYS}
    if isinstance(o, list):
        return [strip_ts(v) for v in o]
    return o


def git(*a):
    return subprocess.run(['git', *a], cwd=REPO, capture_output=True, text=True).stdout


def restore_timestamp_only(folder, clean_before):
    """Restore tracked files that were clean before the build and now differ only in timestamp fields."""
    restored = []
    for f in git('diff', '--name-only', '--', folder).split():
        if f not in clean_before or not f.endswith('.json'):
            continue
        try:
            old, new = json.loads(git('show', f'HEAD:{f}')), json.loads((REPO / f).read_text())
        except ValueError:
            continue
        if strip_ts(old) == strip_ts(new):
            subprocess.run(['git', 'checkout', '--', f], cwd=REPO, check=True); restored.append(f)
    return restored


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('icao', nargs='*'); ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--update-unsigned', action='store_true')
    a = ap.parse_args()
    todo = [x.upper() for x in a.icao] or airports()
    rep, failed = {'run_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'commit': git('rev-parse', '--short', 'HEAD').strip(), 'airports': {}}, False
    for icao in todo:
        b = AIRPORTS / icao.lower(); folder = str(b.relative_to(REPO))
        tracked = set(git('ls-files', '--', folder).split()); dirty = set(git('diff', '--name-only', '--', folder).split())
        r = {'signed': signed(icao)}
        if not a.no_build:
            p = subprocess.run([sys.executable, str(REPO / 'tools/build_airport.py'), icao], cwd=REPO, capture_output=True, text=True)
            r['build_rc'] = p.returncode
            if p.returncode:
                r.update(result='BUILD_FAILED', tail=(p.stdout + p.stderr)[-1500:]); failed |= r['signed']
                rep['airports'][icao] = r; print(f'{icao}: BUILD FAILED'); continue
            r['restored_timestamp_only'] = len(restore_timestamp_only(folder, tracked - dirty))
        now = metrics(icao); bp = b / 'review/regress_baseline.json'
        if not bp.exists():
            bp.write_text(json.dumps({'icao': icao, 'recorded_at': rep['run_at'], 'commit': rep['commit'], 'metrics': now}, indent=1) + '\n')
            r.update(result='new', metrics=now)
        else:
            base = json.loads(bp.read_text()); d = diff(base['metrics'], now)
            r.update(changes=d, result='same' if not d else ('FAIL_SIGNED_CHANGED' if r['signed'] else 'changed'))
            if d and r['signed']:
                failed = True
            elif d and a.update_unsigned:
                bp.write_text(json.dumps({'icao': icao, 'recorded_at': rep['run_at'], 'commit': rep['commit'], 'metrics': now}, indent=1) + '\n')
                r['result'] = 'changed (baseline updated)'
        rep['airports'][icao] = r
        print(f"{icao}: {r['result']}{' (signed)' if r['signed'] else ''}" + ''.join(f"\n   {c['key']}: {c['baseline']} -> {c['now']}" for c in r.get('changes', [])))
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(json.dumps(rep, indent=1))
    print('REGRESS ' + ('FAIL: a signed airport changed or failed to build' if failed else 'OK'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
