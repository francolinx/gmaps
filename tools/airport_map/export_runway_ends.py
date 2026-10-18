"""Export NASR runway-end positions, in each airport map's local metre frame, for the display's ledger overlay.

  python3 tools/airport_map/export_runway_ends.py            # -> ui_scaffold/runway_ends.json (all airports with a georef + map_meta)

Display only: the demo page uses this to tint a runway whose ledger has open authorizations (recorded clearances), on the
reconstructed diagram. No positions, no tracks, no predictions. Source: NASR runway-end lat/lon from data/airports/<icao>/georef.json,
projected with the same local equirectangular frame as the chart (map_meta.frame), so x = east, y = south of the ARP like chart.svg.
Width is a display constant (60 m band), not a surveyed value.
"""
import json, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'ui_scaffold/runway_ends.json'


def project(frame, lat, lon):
    lat0, lon0, M, N = frame['lat0'], frame['lon0'], frame['M'], frame['N']
    east = math.radians(lon - lon0) * N * math.cos(math.radians(lat0))
    north = math.radians(lat - lat0) * M
    return round(east, 1), round(-north, 1)


def main():
    out = {'_about': 'NASR runway ends in each chart\'s local metre frame (x east, y south of the ARP). Display only; see tools/airport_map/export_runway_ends.py.',
           'airports': {}}
    for mm in sorted((ROOT / 'ui_scaffold/maps').glob('*/map_meta.json')):
        icao = mm.parent.name
        gf = ROOT / 'data/airports' / icao / 'georef.json'
        if not gf.exists():
            continue
        meta, g = json.loads(mm.read_text()), json.loads(gf.read_text())
        ends = {e['id']: e for e in g.get('runway_ends', [])}
        runways = []
        for r in g.get('runways', []):
            a, b = r['runway'].split('/')
            if a not in ends or b not in ends:
                continue
            pa, pb = project(meta['frame'], ends[a]['nasr_lat'], ends[a]['nasr_lon']), project(meta['frame'], ends[b]['nasr_lat'], ends[b]['nasr_lon'])
            runways.append({'id': f'{a}-{b}', 'ends': [{'id': a, 'x': pa[0], 'y': pa[1]}, {'id': b, 'x': pb[0], 'y': pb[1]}],
                            'nasr_length_ft': r.get('nasr_length_ft'), 'source': f"NASR {g.get('nasr_cycle', '')} runway ends ({ends[a].get('nasr_position_source', '')})"})
        out['airports'][icao] = {'icao': meta['icao'], 'runways': runways, 'frame': meta['frame']}
        print(f'{icao}: {len(runways)} runways', file=sys.stderr)
    OUT.write_text(json.dumps(out, indent=1))
    print(f'-> {OUT}', file=sys.stderr)


if __name__ == '__main__':
    main()
