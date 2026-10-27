#!/usr/bin/env python3
"""Build the animated LaGuardia scene (ui_scaffold/lga/): airport geometry + the six scripted SIM scenarios placed on it.

SIMULATED. The traffic is the fictional scripted SIM-1 scenarios (callsigns SimAir 212 / Rescue 7), NOT any real event.
What is real: the LaGuardia layout. Runways, taxiways, taxilanes, aprons and hold positions come from OpenStreetMap
(data/airports/klga/osm/, ODbL, attribution shown on the page), projected into the FAA-chart frame of
data/airports/klga/map/map_meta.json (local equirectangular, metres, x east / y north here).

How a SIM-1 scenario is placed on LGA (display transform only; nothing is predicted on LGA geometry):
  * SIM runway 18/36 (2,400 m, landing 36 = +x)  -> LGA runway 04/22 (landing 04, SW -> NE). Along-track distance is mapped
    piecewise-linearly so the SIM crossing point (x = 1500 m) lands on the G crossing (two segments, ~0.87 and ~0.92 scale).
  * SIM taxiway K (x = 1500)                       -> LGA taxiway G, measured along G's own centerline from the crossing.
    The SIM hold lines (75 m either side) are mapped onto the LGA hold positions found on G, so "passed the hold line" in the
    simulation is drawn at the real hold bar.
  * The look-ahead (frames[].combined) is the SIM-1 run unchanged (tools/run_sim_episode.py); it is computed on SIM-1
    geometry and only DRAWN on LGA. The ledger IS re-run on LGA identifiers: the scripted radio relabelled
    ("runway three six" -> "runway four", "Kilo" -> "Golf"), parsed and ledgered against data/airports/klga.graph.json.
Taxiway G was chosen because it crosses 04/22 at a right angle at the same fraction of the landing roll as SIM's K.
Taxiway D is avoided on purpose (the repo's historical LGA fixture is a crossing at D; this scene does not depict it).

usage: python3 tools/build_lga_scene.py      -> ui_scaffold/lga/airport.json, ui_scaffold/lga/<scenario>.json
"""
import json, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'tools')]
from gmaps_core.airport import Airport             # noqa: E402
from gmaps_core.parser import parse                # noqa: E402
from gmaps_core.ledger import Ledger               # noqa: E402
from run_sim_episode import run as run_sim         # noqa: E402

OUT = ROOT / 'ui_scaffold' / 'lga'
SCENARIOS = ['SIM_E1_CROSS_DURING_ROLLOUT', 'SIM_E2_SAME_PATHS_LATER', 'SIM_E3_STOP_NOT_OBSERVED', 'SIM_E4_FEED_LOSS',
             'SIM_E5_HOLD_NOT_SLOWING', 'SIM_E6_HOLD_OK']
RUNWAY, TAXIWAY, TAXIWAY_OSM_ID = '04/22', 'G', 5822271
SIM_RWY_LEN, SIM_X_CROSS, SIM_HOLD = 2400.0, 1500.0, 75.0
RELABEL = [('runway three six', 'runway four'), ('Runway three six', 'Runway four'), ('at Kilo', 'at Golf')]
r1 = lambda v: round(v, 1)


# ------------------------------------------------------------------ geometry
meta = json.loads((ROOT / 'data/airports/klga/map/map_meta.json').read_text())
FR = meta['frame']


def proj(lon, lat):
    return (math.radians(lon - FR['lon0']) * FR['N'] * math.cos(math.radians(FR['lat0'])), math.radians(lat - FR['lat0']) * FR['M'])


def cum(pts):
    out = [0.0]
    for a, b in zip(pts, pts[1:]):
        out.append(out[-1] + math.dist(a, b))
    return out


def at(pts, c, s):
    """point and unit tangent at arc length s (clamped extrapolation beyond the ends)."""
    i = max(0, min(len(pts) - 2, next((k for k in range(len(c) - 1) if c[k + 1] >= s), len(c) - 2)))
    a, b = pts[i], pts[i + 1]
    L = math.dist(a, b) or 1.0
    u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
    d = s - c[i]
    return (a[0] + u[0] * d, a[1] + u[1] * d), u


def seg_x(p1, p2, p3, p4):
    d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
    if abs(d) < 1e-9:
        return None
    t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d
    u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d
    return (t, u) if 0 <= t <= 1 and 0 <= u <= 1 else None


def dist_to_poly(p, pts, c):
    best = (1e9, 0.0)
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1.0
        t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
        q = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        d = math.dist(p, q)
        if d < best[0]:
            best = (d, c[i] + t * math.sqrt(L2))
    return best


def build_airport():
    g = json.loads((ROOT / 'data/airports/klga/osm/osm_aeroways.geojson').read_text())
    A = {'runways': [], 'taxiways': [], 'taxilanes': [], 'aprons': [], 'holds': [], 'stands': [], 'stopways': []}
    for f in g['features']:
        p, geo = f['properties'], f['geometry']
        kind = p['aeroway']
        if geo['type'] == 'LineString':
            pts = [[r1(v) for v in proj(*c)] for c in geo['coordinates']]
        elif geo['type'] == 'Polygon':
            pts = [[r1(v) for v in proj(*c)] for c in geo['coordinates'][0]]
        else:
            pts = [r1(v) for v in proj(*geo['coordinates'])]
        rec = {'id': f"osm:{p['osm_type']}/{p['osm_id']}", 'ref': p.get('ref'), 'pts': pts}
        if kind == 'runway':
            rec['width_m'] = float(p['tags'].get('width', 45))
            A['runways'].append(rec)
        elif kind == 'taxiway':
            A['taxiways'].append(rec)
        elif kind == 'taxilane':
            A['taxilanes'].append(rec)
        elif kind == 'apron':
            A['aprons'].append(rec)
        elif kind == 'holding_position':
            A['holds'].append(rec)
        elif kind == 'parking_position':
            A['stands'].append(rec)
        elif kind == 'stopway':
            A['stopways'].append(rec)
    xs = [q[0] for k in ('runways', 'taxiways', 'aprons') for r in A[k] for q in r['pts']]
    ys = [q[1] for k in ('runways', 'taxiways', 'aprons') for r in A[k] for q in r['pts']]
    A['bounds'] = [r1(min(xs)), r1(min(ys)), r1(max(xs)), r1(max(ys))]
    for r in A['runways']:   # runway end designators at each end, oriented along the runway
        a, b = r['pts'][0], r['pts'][-1]
        lo, hi = r['ref'].split('/')
        hdg_ab = math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360
        # the end you land ON when flying heading h is designated round(h/10); the label sits at the threshold you start from
        start_a = lo if abs(((int(lo) * 10 - hdg_ab) + 180) % 360 - 180) < 45 else hi
        r['ends'] = {start_a: a, (hi if start_a == lo else lo): b}
    A['attribution'] = meta['osm_attribution']
    A['chart_footer'] = meta['footer']
    A['frame'] = {'source': 'data/airports/klga/map/map_meta.json', 'projection': FR['projection'], 'origin': 'NASR airport reference point',
                  'axes': 'metres, x east, y north'}
    return A


# ------------------------------------------------------------------ SIM-1 -> LGA placement
def build_placement(A):
    rw = next(r for r in A['runways'] if r['ref'] == RUNWAY)
    thr04 = rw['ends']['04']
    pts = rw['pts'] if rw['pts'][0] == thr04 else list(reversed(rw['pts']))   # oriented along a runway-04 landing
    c = cum(pts)
    tw = next(t for t in A['taxiways'] if t['id'] == f'osm:way/{TAXIWAY_OSM_ID}')
    tpts, tc = tw['pts'], cum(tw['pts'])
    s_rw = s_tw = None
    for i in range(len(pts) - 1):
        for j in range(len(tpts) - 1):
            x = seg_x(pts[i], pts[i + 1], tpts[j], tpts[j + 1])
            if x:
                s_rw = c[i] + x[0] * math.dist(pts[i], pts[i + 1])
                s_tw = tc[j] + x[1] * math.dist(tpts[j], tpts[j + 1])
    assert s_rw is not None, 'taxiway G does not cross 04/22 in the OSM data'
    cross_pt, _ = at(pts, c, s_rw)
    # SIM "south" side of K (y < 0) = the side of G whose arc length is smaller; pick hold positions on each side of the crossing
    holds = {}
    for h in A['holds']:
        q = h['pts'] if isinstance(h['pts'][0], (int, float)) else [sum(v[0] for v in h['pts']) / len(h['pts']), sum(v[1] for v in h['pts']) / len(h['pts'])]
        d, s = dist_to_poly(q, tpts, tc)
        if d < 25 and 30 < abs(s - s_tw) < 160:
            side = 'south' if s < s_tw else 'north'
            if side not in holds or abs(s - s_tw) < holds[side]['dist']:
                holds[side] = {'dist': r1(abs(s - s_tw)), 'osm': h['id'], 'pt': q}
    for side in ('south', 'north'):
        holds.setdefault(side, {'dist': SIM_HOLD, 'osm': None, 'pt': list(at(tpts, tc, s_tw + (-SIM_HOLD if side == 'south' else SIM_HOLD))[0]), 'note': 'no OSM hold position found on this side; SIM distance used'})
    return {'runway': RUNWAY, 'landing': '04', 'rw_pts': pts, 'rw_cum': c, 'rw_len': c[-1], 's_rw_cross': s_rw,
            'tw_pts': tpts, 'tw_cum': tc, 's_tw_cross': s_tw, 'cross_pt': cross_pt, 'holds': holds, 'taxiway': TAXIWAY, 'taxiway_osm': tw['id']}


def map_runway(Pm, x, y):
    s = (x / SIM_X_CROSS * Pm['s_rw_cross']) if x <= SIM_X_CROSS else Pm['s_rw_cross'] + (x - SIM_X_CROSS) / (SIM_RWY_LEN - SIM_X_CROSS) * (Pm['rw_len'] - Pm['s_rw_cross'])
    p, u = at(Pm['rw_pts'], Pm['rw_cum'], s)
    return (p[0] - u[1] * y, p[1] + u[0] * y)   # SIM +y (north) = left of the landing direction


def map_taxiway(Pm, x, y):
    side = 'south' if y < 0 else 'north'
    h = Pm['holds'][side]['dist']
    d = abs(y)
    m = d / SIM_HOLD * h if d <= SIM_HOLD else h + (d - SIM_HOLD)
    s = Pm['s_tw_cross'] + (-m if side == 'south' else m)
    p, u = at(Pm['tw_pts'], Pm['tw_cum'], s)
    off = x - SIM_X_CROSS
    return (p[0] + u[1] * off, p[1] - u[0] * off)


def zone_poly(Pm, zone):
    """SIM zone Z1 (between the K hold lines) drawn as the runway band between the two LGA hold bars on G."""
    out = []
    for xx, yy in ((zone['xmin'], zone['ymin']), (zone['xmax'], zone['ymin']), (zone['xmax'], zone['ymax']), (zone['xmin'], zone['ymax'])):
        tw = map_taxiway(Pm, SIM_X_CROSS, yy)
        dx = xx - SIM_X_CROSS
        rp, ru = at(Pm['rw_pts'], Pm['rw_cum'], Pm['s_rw_cross'] + dx)
        cp = Pm['cross_pt']
        out.append([r1(tw[0] + (rp[0] - cp[0])), r1(tw[1] + (rp[1] - cp[1]))])
    return out


# ------------------------------------------------------------------ scenarios
def build_scenario(eid, Pm, LGA):
    sim = run_sim(eid)
    tr = json.loads((ROOT / f'episodes/{eid}.transcript.json').read_text())
    led = Ledger(LGA)
    lines, pending = [], sorted(tr['events'], key=lambda e: e['t'])
    for t in range(0, 101):
        while pending and pending[0]['t'] <= t:
            e = pending.pop(0)
            txt = e['text_clean']
            for a, b in RELABEL:
                txt = txt.replace(a, b)
            ev = parse(txt, 'KLGA', speaker_hint='ATC' if e['speaker'] == 'ATC' else 'PILOT', resolver=LGA, event_time=e['t'])
            led.ingest(ev, e['t'])
            lines.append({'t': e['t'], 'speaker': e['speaker'], 'text': txt, 'sim_text': e['text_clean'], 'kind': ev['kind'],
                          'speech_act': ev.get('speech_act_resolved', ev['speech_act']), 'callsigns': ev['actor_candidates'],
                          'unresolved': ev['uncertainty']['unresolved']})
        led.tick(t)
    warnings = [{'id': w['warning_id'], 'rule': w['rule_id'], 'name': w['name'], 'severity': w['severity'], 't': w['t_fired'],
                 'runway': w['runway'], 'explain': w['explain'], 'status': w['status'], 'cleared_at': w.get('cleared_at'),
                 'cleared_by': w.get('cleared_by'), 'cite': w.get('cite')}
                for w in led.warnings if w['severity'] in ('CONFLICT', 'CAUTION')]
    sim_rules = sorted((w['rule_id'], w['t_fired']) for w in sim['ledger']['warnings'] if w['severity'] in ('CONFLICT', 'CAUTION'))
    lga_rules = sorted((w['rule'], w['t']) for w in warnings)
    tracks = {}
    for f in sim['frames']:
        for tid, p in f['positions'].items():
            mp = map_runway(Pm, p['x'], p['y']) if p['class'] == 'aircraft' else map_taxiway(Pm, p['x'], p['y'])
            tracks.setdefault(tid, {'callsign': p['callsign'], 'class': p['class'], 'samples': []})['samples'].append(
                [f['t'], r1(mp[0]), r1(mp[1]), p['age_s'], r1(p['x']), r1(p['y'])])
    look = []
    for f in sim['frames']:
        c = f['combined']
        look.append({'t': f['t'],
                     'raised': [{'type': x['type'], 'callsigns': x['callsigns'], 'lead_s': x.get('lead_s'), 'overlap': x.get('overlap'),
                                 'basis': x.get('basis') or []} for x in c['candidates'] if x.get('raised')],
                     'watch': [{'type': x['type'], 'callsigns': x['callsigns'], 'lead_s': x.get('lead_s'), 'basis': x.get('basis') or []}
                               for x in c['candidates'] if not x.get('raised')],
                     'unavailable': [u['reason'] for u in c['unavailable']],
                     'actors': {tid: {'v': (a.get('estimate') or {}).get('v'), 'status': a.get('status'),
                                      'occ': (a.get('occupancy') or {}).get('status'), 'window': (a.get('occupancy') or {}).get('window')}
                                for tid, a in c.get('actors', {}).items()}})
    # simulated encounter second: same rule as ui_scaffold/demo.html (both fresh, both inside the zone)
    Z = sim['map']['zones']['XSIM:ZONE:Z1']
    inz = lambda p: p and p['age_s'] <= 3 and Z['xmin'] <= p['x'] <= Z['xmax'] and Z['ymin'] <= p['y'] <= Z['ymax']
    hit = next((f['t'] for f in sim['frames'] if inz(f['positions'].get('T-AC1')) and inz(f['positions'].get('T-VH7'))), None)
    stale = [f['t'] for f in sim['frames'] if any(p['age_s'] > 3 for p in f['positions'].values())]
    return {'SIMULATED': True, 'id': eid, 'title': sim['episode']['title'], 'purpose': sim['episode']['purpose'],
            'traffic': 'fictional scripted scenario (SimAir 212, Rescue 7); not a real event',
            'transcript': lines, 'warnings': warnings,
            'ledger_check': {'sim1_rules': sim_rules, 'klga_rules': lga_rules, 'same': sim_rules == lga_rules},
            'tracks': tracks, 'lookahead': look, 'lookahead_model': sim['model'],
            'outcome_t': hit, 'stale_from': stale[0] if stale else None, 'stale_to': stale[-1] if stale else None,
            'zone': zone_poly(Pm, Z)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    A = build_airport()
    Pm = build_placement(A)
    LGA = Airport(ROOT / 'data/airports/klga.graph.json')
    A['placement'] = {'runway': Pm['runway'], 'landing_runway': '04', 'taxiway': Pm['taxiway'], 'taxiway_osm': Pm['taxiway_osm'],
                      'crossing_point': [r1(v) for v in Pm['cross_pt']], 'crossing_from_threshold_04_m': r1(Pm['s_rw_cross']),
                      'runway_length_m': r1(Pm['rw_len']), 'holds': {k: {kk: (r1(vv) if isinstance(vv, float) else vv) for kk, vv in v.items() if kk != 'pt'} | {'pt': [r1(q) for q in v['pt']]} for k, v in Pm['holds'].items()},
                      'landing_path': [[r1(v) for v in p] for p in Pm['rw_pts']],
                      'note': 'SIM-1 scenarios drawn on the LGA layout. Display transform only; the look-ahead is computed on SIM-1 geometry.'}
    (OUT / 'airport.json').write_text(json.dumps(A, separators=(',', ':')) + '\n')
    idx = []
    for eid in SCENARIOS:
        s = build_scenario(eid, Pm, LGA)
        (OUT / f'{eid}.json').write_text(json.dumps(s, separators=(',', ':')) + '\n')
        idx.append({'id': eid, 'title': s['title'], 'outcome_t': s['outcome_t'], 'warnings': [(w['rule'], w['t']) for w in s['warnings']],
                    'ledger_same_as_sim1': s['ledger_check']['same']})
        print(eid, 'outcome', s['outcome_t'], 'warnings', [(w['rule'], w['t']) for w in s['warnings']], 'ledger same as SIM-1:', s['ledger_check']['same'])
    (OUT / 'index.json').write_text(json.dumps(idx, indent=1) + '\n')
    print('placement:', json.dumps({k: v for k, v in A['placement'].items() if k != 'landing_path'}))


if __name__ == '__main__':
    main()
