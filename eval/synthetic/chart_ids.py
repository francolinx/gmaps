"""Identifier + topology view of a built airport, for SYNTHETIC EVALUATION ONLY (eval/; never imported by the runtime).

Same usability as a v1 graph (identifier lookup and topology review; no positions, routing or prediction are exposed):
  runway ends + physical runways      FAA NASR rows (data/airports/<icao>/sources/nasr)
  taxiway names                       the FAA chart only (review/taxiway_vocabulary.json; Franco R1)
  runway-runway intersections         NASR runway end-to-end segments that cross (topology)
  taxiway-runway crossings            OSM taxiway geometry (ODbL) whose ref is a chart name, crossing a NASR runway segment
  hot spots                           chart taxiway labels inside each hot-spot outline
Geometry is used here only to derive which identifiers meet; nothing positional leaves this module.
"""
import csv, json, math, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _seg_cross(p1, p2, q1, q2):
    d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
    if abs(d) < 1e-12:
        return False
    t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d
    u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
    return 0 < t < 1 and 0 < u < 1


class ChartIdentifiers:
    def __init__(self, icao):
        self.icao = icao.upper(); base = ROOT / 'data/airports' / icao.lower()
        lat0 = lon0 = None
        en = lambda lat, lon: ((lon - lon0) * 111320 * math.cos(math.radians(lat0)), (lat - lat0) * 110540)
        rows = [r for r in csv.DictReader(open(base / 'sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8'))
                if r['LAT_DECIMAL'].strip() and not r['RWY_END_ID'].startswith('H')]
        lat0, lon0 = float(rows[0]['LAT_DECIMAL']), float(rows[0]['LONG_DECIMAL'])
        self.surfaces, self.ends, self.nodes, self.segments, self.holds = {}, {}, {}, {}, {}
        segs = {}
        for r in rows:
            rid = f"{self.icao}:RWY:{r['RWY_ID'].replace('/', '-')}"
            self.surfaces[rid] = {'id': rid, 'kind': 'runway', 'identifier': r['RWY_ID']}
            self.ends[f"{self.icao}:END:{r['RWY_END_ID']}"] = {'id': f"{self.icao}:END:{r['RWY_END_ID']}", 'identifier': r['RWY_END_ID'], 'runway_id': rid}
            segs.setdefault(rid, []).append(en(float(r['LAT_DECIMAL']), float(r['LONG_DECIMAL'])))
        self.vocab = json.loads((base / 'review/taxiway_vocabulary.json').read_text())['taxiways']
        for tw in self.vocab:
            self.surfaces[f'{self.icao}:TWY:{tw}'] = {'id': f'{self.icao}:TWY:{tw}', 'kind': 'taxiway', 'identifier': tw}
        rw = [(k, v) for k, v in segs.items() if len(v) == 2]
        for i, (a, pa) in enumerate(rw):
            for b, pb in rw[i + 1:]:
                if _seg_cross(pa[0], pa[1], pb[0], pb[1]):
                    nid = f'{self.icao}:N:{a.split(":")[-1]}_{b.split(":")[-1]}'
                    self.nodes[nid] = {'id': nid, 'kind': 'runway_intersection', 'surface_ids': [a, b]}
        self.crossings = {}
        osm = json.loads((base / 'osm/osm_aeroways.geojson').read_text())['features']
        for f in osm:
            ref = f['properties'].get('ref')
            if f['properties'].get('aeroway') != 'taxiway' or f['geometry']['type'] != 'LineString' or ref not in self.vocab:
                continue
            pts = [en(c[1], c[0]) for c in f['geometry']['coordinates']]
            for rid, pr in rw:
                if any(_seg_cross(p, q, pr[0], pr[1]) for p, q in zip(pts, pts[1:])):
                    self.crossings.setdefault(ref, set()).add(rid)
        for tw, rids in self.crossings.items():
            for rid in rids:
                nid = f'{self.icao}:N:{tw}_{rid.split(":")[-1]}'
                self.nodes[nid] = {'id': nid, 'kind': 'runway_taxiway_junction', 'surface_ids': [f'{self.icao}:TWY:{tw}', rid]}
        raw = json.loads((base / 'build/layers.raw.json').read_text())
        shapes = [o for o in raw['objects'] if o['class'] == 'hot_spot' and o['stroke'] and not o['fill'] and o.get('beziers') and o['long'] >= 4 and o['aspect'] <= 6]
        self.hot_spots = {}
        for l in raw['labels']:
            if l['class'] == 'hot_spot_label' and shapes:
                c = ((l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2)
                o = min(shapes, key=lambda o: math.dist(o['centre'], c)); b = o['bbox']
                inside = sorted({t['text'] for t in raw['labels'] if t['class'] == 'taxiway_candidate' and t['text'] in self.vocab
                                 and b[0] <= (t['bbox'][0] + t['bbox'][2]) / 2 <= b[2] and b[1] <= (t['bbox'][1] + t['bbox'][3]) / 2 <= b[3]})
                self.hot_spots[l['text']] = inside

    @property
    def version(self):
        return {'icao': self.icao, 'dataset_version': 'chart identifiers (synthetic evaluation only)', 'status': 'identifiers and topology only; no predictions'}

    def runway_end_candidates(self, designator):
        if not designator:
            return []
        m = re.fullmatch(r'0?(\d{1,2})([LRC]?)', designator)
        if not m:
            return []
        num, suf = int(m.group(1)), m.group(2)
        return sorted(e['id'] for e in self.ends.values() if (mm := re.fullmatch(r'(\d{2})([LRC]?)', e['identifier'])) and int(mm.group(1)) == num and (suf == '' or mm.group(2) == suf))

    def physical_runway(self, end_id):
        return self.ends[end_id]['runway_id'] if end_id in self.ends else None

    def taxiway_candidates(self, letter):
        return [f'{self.icao}:TWY:{letter}'] if letter in self.vocab else []

    def intersecting_runways(self):
        return {tuple(sorted(n['surface_ids'])) for n in self.nodes.values() if n['kind'] == 'runway_intersection'}

    def runway_crossings_for_taxiway(self, taxiway_id):
        return [(n['id'], s) for n in self.nodes.values() if n['kind'] == 'runway_taxiway_junction' and taxiway_id in n['surface_ids'] for s in n['surface_ids'] if s != taxiway_id]

    def route_runway_check(self, route_letters):
        out = []
        for w in route_letters or []:
            tid = f'{self.icao}:TWY:{w}'
            if w not in self.vocab:
                out.append({'word': w, 'status': 'not_in_inventory', 'crossings': []}); continue
            xs = self.runway_crossings_for_taxiway(tid)
            out.append({'word': w, 'taxiway_id': tid, 'status': 'crossing_recorded' if xs else 'no_crossing_data_in_draft_graph', 'crossings': [{'node_id': a, 'runway_id': b} for a, b in xs]})
        return out

    def holds_for(self, protected_runway_id, approach_taxiway_id=None):
        return []
