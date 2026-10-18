"""Build an airport's reconstructed display map end to end, with the same review checklist for every airport.

  python3 tools/build_airport.py KHWD [--refresh-osm] [--screens]

Stages (each is a standalone tool in tools/airport_map/; the first failure stops the build):
  1 dtpp.py        current d-TPP cycle, the airport diagram PDF -> data/airports/sources/, source_chart.json
  2 nasr.py        NASR cycle in effect; only this airport's rows committed (APT_BASE, APT_RWY, APT_RWY_END)
  3 extract.py     vector layers + labels, generic style file tools/airport_map/styles/faa_apd.json
  4 graticule.py   graticule values from the PDF text layer (a human-confirmed graticule.json is kept as is)
  5 checklist.py   review.json from the generic template (never overwrites an existing one)
  6 georef.py      graticule fit; runway ends vs NASR (held out); 6b runway_ends_review.py along/across-track per end
  7 provenance.py  provenance.json + the map footer line
  8 overlay_check.py   FAA ink vs reconstruction at 300 dpi
  9 osm.py         fetch (only if missing, or --refresh-osm) + build + name cross-check; OSM kept in its own folder (ODbL)
 10 provenance.py  again, to carry the OSM block
 11 cycle_check.py today vs the chart window; FAA metafile lists the same APD; served PDF has the same sha256
 12 build_map.py   map assets for ui_scaffold/airport_map.html?icao=<icao>
 13 map_screens.py (with --screens: full + full_osm_on; --screens-all adds every review view; needs the local UI server on :8765)
No airport-specific code: anything a new airport breaks is fixed in the generic tools or style file.
"""
import argparse, json, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = REPO / 'tools/airport_map'
PY = str(REPO / '.venv/bin/python') if (REPO / '.venv/bin/python').exists() else sys.executable   # pdfminer, Pillow, numpy live in .venv


def run(name, args):
    print(f'== {name}', flush=True)
    r = subprocess.run([PY, *args], cwd=REPO, capture_output=True, text=True)
    tail = (r.stdout.strip().splitlines() or [''])[-1]
    print('   ' + tail[:300])
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-3000:], sep='\n')
        raise SystemExit(f'stage {name} failed (exit {r.returncode})')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('icao'); ap.add_argument('--refresh-osm', action='store_true'); ap.add_argument('--screens', action='store_true', help='full.png + full_osm_on.png')
    ap.add_argument('--screens-all', action='store_true', help='with --screens: every review view too')
    a = ap.parse_args()
    icao = a.icao.upper(); base = REPO / 'data/airports' / icao.lower()
    run('1 dtpp', [str(T / 'dtpp.py'), '--icao', icao]) if not (base / 'graticule.json').exists() or (base / 'source_chart.json').exists() else print('== 1 dtpp: kept (hand-assembled source record)')
    src = json.loads((base / 'source_chart.json').read_text()) if (base / 'source_chart.json').exists() else None
    pdf = REPO / 'data/airports' / src['local_path'] if src else next(REPO.glob(f'data/airports/sources/{icao[1:].lower()}_faa_*.pdf'))
    run('2 nasr', [str(T / 'nasr.py'), '--icao', icao])
    if src:   # the checklist first, so extraction can score its items on the very first build
        run('5 checklist', [str(T / 'checklist.py'), '--icao', icao])
    run('3 extract', [str(T / 'extract.py'), str(pdf), '--icao', icao])
    hint = base / 'build/rotation_hint.json'
    if hint.exists():   # no text layer: rotation found from decoded outline text on an earlier pass
        run('3 extract (rotation hint)', [str(T / 'extract.py'), str(pdf), '--icao', icao, '--rotation', str(json.loads(hint.read_text())['content_rotation_deg'])])
    r4 = subprocess.run([PY, str(T / 'graticule.py'), '--icao', icao], cwd=REPO, capture_output=True, text=True)
    if r4.returncode and 'ROTATION:' in r4.stdout + r4.stderr and hint.exists() and not hint.read_text().startswith('{"used"'):
        rot = json.loads(hint.read_text())['content_rotation_deg']
        print(f'== 3 extract again: chart has no text layer; rotation {rot} from decoded outline text')
        run('3 extract (rotation from decoded text)', [str(T / 'extract.py'), str(pdf), '--icao', icao, '--rotation', str(rot)])
    run('4 graticule', [str(T / 'graticule.py'), '--icao', icao])
    if not src:
        run('5 checklist', [str(T / 'checklist.py'), '--icao', icao])
    run('6 georef', [str(T / 'georef.py'), '--icao', icao])
    run('6b runway ends review', [str(T / 'runway_ends_review.py'), '--icao', icao])
    run('7 provenance', [str(T / 'provenance.py'), '--icao', icao])
    r = subprocess.run([PY, str(T / 'overlay_check.py'), '--icao', icao], cwd=REPO, capture_output=True, text=True)
    print('== 8 overlay_check\n   ' + ' '.join(l.strip() for l in r.stdout.splitlines() if '"covered_pct"' in l or '"extra_pct"' in l or '"pass"' in l))
    if not (base / 'osm/overpass_raw.json').exists() or a.refresh_osm:
        run('9a osm fetch', [str(T / 'osm.py'), 'fetch', '--icao', icao])
    run('9b osm build', [str(T / 'osm.py'), 'build', '--icao', icao])
    run('10 provenance', [str(T / 'provenance.py'), '--icao', icao])
    run('11 cycle_check', [str(T / 'cycle_check.py'), '--icao', icao])
    run('12 build_map', [str(T / 'build_map.py'), '--icao', icao])
    if a.screens:
        run('13 map_screens', [str(T / 'map_screens.py'), '--icao', icao] + ([] if a.screens_all else ['--only', 'full', '--only', 'full_osm_on']))
    rv = json.loads((base / 'review.json').read_text())
    print('\nchecklist', icao, json.dumps({i['id']: i['status'] for i in rv['items']}), '| signed off:', rv.get('signed_off_by'))
    print(f'map: http://localhost:8765/airport_map.html?icao={icao.lower()}')


if __name__ == '__main__':
    main()
