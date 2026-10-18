"""A2: georeference an extracted FAA airport diagram and measure the error against NASR. Display geometry only.

  python3 tools/airport_map/georef.py --icao KLGA

Fit (independent of NASR runway ends): affine transform page (north-up pt) -> local metres (east, north), from the
graticule control points = intersections of the extracted graticule lines with the human-confirmed graticule values
(data/airports/<icao>/graticule.json). Local frame: east/north metres around the NASR airport reference point,
WGS84 meridional / prime-vertical radii (equirectangular; error far below drawing precision over ~3 km).

Validation (held out): each diagram runway end (the end of the black runway bar, identified by its runway-end label)
is transformed and compared with the NASR runway-end position of the same identifier. Residuals in metres, also
split along / across the runway. For comparison only: an affine fitted to the NASR ends themselves, scored
leave-one-out. Extra held-out point: NASR helipad H1 vs the diagram's H symbol.
Writes data/airports/<icao>/georef.json, review/GEOREF_REVIEW.md, and scores review.json items runway_ends,
runway_identifiers. Stdlib only.
"""
import argparse, csv, json, math, re, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WGS84_A, WGS84_E2 = 6378137.0, 6.69437999014e-3
FT = 0.3048


def radii(lat0):
    s = math.sin(math.radians(lat0))
    w = math.sqrt(1 - WGS84_E2 * s * s)
    return WGS84_A * (1 - WGS84_E2) / w ** 3, WGS84_A / w          # meridional M, prime vertical N


class Local:
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.M, self.N = radii(lat0)

    def en(self, lat, lon):
        return (math.radians(lon - self.lon0) * self.N * math.cos(math.radians(self.lat0)), math.radians(lat - self.lat0) * self.M)

    def ll(self, e, n):
        return (self.lat0 + math.degrees(n / self.M), self.lon0 + math.degrees(e / (self.N * math.cos(math.radians(self.lat0)))))


def solve3(A, b):
    """Solve a 3x3 system (Cramer's rule)."""
    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    D = det(A)
    if abs(D) < 1e-12:
        raise ValueError('singular fit')
    out = []
    for i in range(3):
        m = [row[:] for row in A]
        for r in range(3):
            m[r][i] = b[r]
        out.append(det(m) / D)
    return out


def fit_affine(src, dst):
    """Least-squares affine: dst = [a b c; d e f] . [x y 1]. Needs >= 3 non-collinear points."""
    A = [[0.0] * 3 for _ in range(3)]; bx = [0.0] * 3; by = [0.0] * 3
    for (x, y), (u, v) in zip(src, dst):
        r = (x, y, 1.0)
        for i in range(3):
            for j in range(3):
                A[i][j] += r[i] * r[j]
            bx[i] += r[i] * u; by[i] += r[i] * v
    return solve3(A, bx) + solve3(A, by)


def apply(T, p):
    a, b, c, d, e, f = T
    return (a * p[0] + b * p[1] + c, d * p[0] + e * p[1] + f)


def describe(T):
    a, b, c, d, e, f = T
    sx, sy = math.hypot(a, d), math.hypot(b, e)                 # metres per pt along page x / page y
    rot = math.degrees(math.atan2(d, a))                         # page +x direction vs east
    ortho = math.degrees(math.acos(max(-1, min(1, (a * b + d * e) / (sx * sy))))) # angle between page axes on the ground
    return {'m_per_pt_x': round(sx, 4), 'm_per_pt_y': round(sy, 4), 'page_x_vs_east_deg': round(rot, 3), 'page_axes_angle_deg': round(ortho, 3),
            'chart_scale_1_to': round(sx / (0.0254 / 72))}


def read_csv(p):
    with open(p, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def norm_id(s):
    m = re.match(r'^0*(\d+)([LRC]?)$', s)
    return f'{int(m.group(1)):02d}{m.group(2)}' if m else s


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True)
    a = ap.parse_args()
    icao = a.icao.upper(); base = REPO / 'data/airports' / icao.lower()
    raw = json.loads((base / 'build/layers.raw.json').read_text())
    objs, labels, meta = raw['objects'], raw['labels'], raw['meta']
    grat = json.loads((base / 'graticule.json').read_text())
    if grat['pdf_sha256'] != meta['pdf_sha256']:
        raise SystemExit('graticule.json was confirmed for a different PDF')
    nasr = base / 'sources/nasr'
    apt = read_csv(nasr / 'APT_BASE.csv')[0]; rwys = read_csv(nasr / 'APT_RWY.csv'); ends = read_csv(nasr / 'APT_RWY_END.csv')
    # NASR rows without a position (e.g. KORD "10X", length 0) are not runways on the ground: left out, and recorded
    unplaced = sorted({e['RWY_ID'] for e in ends if not (e.get('LAT_DECIMAL') or '').strip() or not (e.get('LONG_DECIMAL') or '').strip()})
    ends = [e for e in ends if e['RWY_ID'] not in unplaced]; rwys = [r for r in rwys if r['RWY_ID'] not in unplaced]
    L = Local(float(apt['LAT_DECIMAL']), float(apt['LONG_DECIMAL']))

    # graticule lines -> two meridian x's and two parallel y's (page pt, north-up frame)
    gl = [o for o in objs if o['class'] == 'graticule_line']
    def lines(vert):   # collinear sets with >= 20 pt total drawn length (excludes short tick stubs)
        segs = [(o['centre'][0] if vert else o['centre'][1], o['long']) for o in gl if (abs(abs(o['axis_deg']) - 90) < 5) == vert]
        out = []
        for g in _groups([v for v, _ in segs], 1.0):
            total = sum(l for v, l in segs if g[0] - 1e-9 <= v <= g[-1] + 1e-9)
            if total >= 20.0:
                out.append(sum(g) / len(g))
        return out
    if all('line_pt_y' in q for q in grat['parallels']) and all('line_pt_x' in q for q in grat['meridians']):
        # values read from the PDF text layer, each already tied to its line (tools/airport_map/graticule.py)
        ctrl = [{'page_pt': [q['line_pt_x'], p_['line_pt_y']], 'lat': p_['lat'], 'lon': q['lon'], 'label': f"{p_['value']}/{q['value']}"}
                for p_ in grat['parallels'] for q in grat['meridians']]
    else:
        # human-transcribed values by position (top/bottom, left/right), tied to the extracted lines by order
        mx, py = lines(True), lines(False)
        if len(mx) != len(grat['meridians']) or len(py) != len(grat['parallels']):
            raise SystemExit(f'graticule lines found: {len(mx)} meridians, {len(py)} parallels; expected {len(grat["meridians"])}, {len(grat["parallels"])}')
        mer = {m['position']: m['lon'] for m in grat['meridians']}; par = {p['position']: p['lat'] for p in grat['parallels']}
        xs = {'left': mx[0], 'right': mx[-1]}; ys = {'top': py[0], 'bottom': py[-1]}
        ctrl = [{'page_pt': [round(xs[mp], 3), round(ys[pp], 3)], 'lat': par[pp], 'lon': mer[mp], 'label': f'{pp}/{mp}'} for pp in ys for mp in xs]
    grat_source = grat.get('source') or ('human_transcription' if grat.get('confirmed_by') else 'unconfirmed')
    if grat_source not in ('pdf_text_layer', 'outline_glyph_decode') and not grat.get('confirmed_by'):
        raise SystemExit('graticule values are neither from the PDF (text layer or exact glyph decode) nor confirmed by a person; not georeferencing')
    T = fit_affine([c['page_pt'] for c in ctrl], [L.en(c['lat'], c['lon']) for c in ctrl])
    for c in ctrl:
        e, n = apply(T, c['page_pt']); te, tn = L.en(c['lat'], c['lon'])
        c['fit_residual_m'] = round(math.hypot(e - te, n - tn), 3)

    # diagram runway ends, identified by the runway-end labels beyond each end
    end_pts = []
    for r in (o for o in objs if o['class'] == 'runway'):
        cx, cy = r['centre']; u = r['u']
        pts = [tuple(map(float, q.split(','))) for q in re.findall(r'-?\d+\.\d+,-?\d+\.\d+', r['d'])]
        al = [(p[0] - cx) * u[0] + (p[1] - cy) * u[1] for p in pts]
        for t in (min(al), max(al)):
            end_pts.append({'runway_index': r['index'], 'page_pt': (cx + t * u[0], cy + t * u[1]), 'axis_u': u})
    rl = [l for l in labels if l['class'] == 'runway_end']
    by_id = {norm_id(e['RWY_END_ID']): e for e in ends if e['RWY_END_ID'] != 'H1'}
    rw_centre = {r['index']: r['centre'] for r in objs if r['class'] == 'runway'}
    for ep in end_pts:
        # the label that names this end: nearest one whose NASR end points the way this runway runs from here
        # (true alignment within 15 degrees); plain nearest only if none fits (parallel runways defeat plain nearest)
        p0 = apply(T, ep['page_pt']); p1 = apply(T, rw_centre[ep['runway_index']])
        brg = (math.degrees(math.atan2(p1[0] - p0[0], p1[1] - p0[1])) + 360) % 360
        dist = lambda l: math.hypot((l['bbox'][0] + l['bbox'][2]) / 2 - ep['page_pt'][0], (l['bbox'][1] + l['bbox'][3]) / 2 - ep['page_pt'][1])
        if not rl:   # no runway-end labels (chart without a text layer): identify the end by the nearest NASR end after the graticule fit
            ep['id'] = norm_id(min(by_id.values(), key=lambda n: math.dist(p0, L.en(float(n['LAT_DECIMAL']), float(n['LONG_DECIMAL']))))['RWY_END_ID'])
            ep['identified_by'] = 'nearest NASR end (no runway-end labels on the chart)'
            continue
        fits = [l for l in rl if norm_id(l['text']) in by_id and abs((brg - float(by_id[norm_id(l['text'])]['TRUE_ALIGNMENT']) + 180) % 360 - 180) <= 15]
        ux, uy = ep['axis_u']   # among those, the one closest to this runway's own axis line (parallel runways share an alignment)
        perp = lambda l: abs(-((l['bbox'][0] + l['bbox'][2]) / 2 - ep['page_pt'][0]) * uy + ((l['bbox'][1] + l['bbox'][3]) / 2 - ep['page_pt'][1]) * ux)
        near_fits = [l for l in fits if dist(l) <= 60]
        ep['id'] = norm_id((min(near_fits, key=lambda l: perp(l) + 0.2 * dist(l)) if near_fits else min(fits or rl, key=dist))['text'])
    rows = []
    for ep in sorted(end_pts, key=lambda e: e['id']):
        n = by_id.get(ep['id'])
        if not n:
            rows.append({'id': ep['id'], 'error': 'no NASR end with this identifier'}); continue
        e_, n_ = apply(T, ep['page_pt']); te, tn = L.en(float(n['LAT_DECIMAL']), float(n['LONG_DECIMAL']))
        de, dn = e_ - te, n_ - tn
        # along/across the runway: exact bearing from this NASR end to the opposite NASR end
        # (NASR TRUE_ALIGNMENT is rounded to whole degrees, worth several metres across over 2 km)
        rw_id = n['RWY_ID']; other = next(x for x in ends if x['RWY_ID'] == rw_id and x['RWY_END_ID'] != n['RWY_END_ID'])
        oe, on = L.en(float(other['LAT_DECIMAL']), float(other['LONG_DECIMAL']))
        brg = math.atan2(oe - te, on - tn); ue, un = math.sin(brg), math.cos(brg)
        lat, lon = L.ll(e_, n_)
        rows.append({'id': ep['id'], 'nasr_lat': float(n['LAT_DECIMAL']), 'nasr_lon': float(n['LONG_DECIMAL']), 'diagram_lat': round(lat, 8), 'diagram_lon': round(lon, 8),
                     'diagram_page_pt': [round(v, 3) for v in ep['page_pt']], 'd_east_m': round(de, 2), 'd_north_m': round(dn, 2),
                     'residual_m': round(math.hypot(de, dn), 2), 'along_m': round(de * ue + dn * un, 2), 'across_m': round(-de * un + dn * ue, 2),
                     'nasr_true_alignment_deg': float(n['TRUE_ALIGNMENT']), 'nasr_end_to_end_bearing_deg': round((math.degrees(brg) + 360) % 360, 3), 'nasr_position_source': n.get('RWY_END_PSN_SOURCE'), 'nasr_position_date': n.get('RWY_END_PSN_DATE')})
    ok_rows = [r for r in rows if 'residual_m' in r]

    # runway lengths and true bearings from the diagram vs NASR
    geo = []
    for rw in rwys:
        if rw['RWY_ID'] == 'H1':
            continue
        pair = [r for r in ok_rows if r['id'] in [norm_id(x) for x in rw['RWY_ID'].split('/')]]
        if len(pair) != 2:   # a runway end found twice or not at all: reported, and runway_identifiers fails below
            geo.append({'runway': rw['RWY_ID'], 'error': f'{len(pair)} diagram ends matched (expected 2)'}); continue
        e1, e2 = pair
        p1, p2 = apply(T, e1['diagram_page_pt']), apply(T, e2['diagram_page_pt'])
        length = math.hypot(p2[0] - p1[0], p2[1] - p1[1]); brg = (math.degrees(math.atan2(p2[0] - p1[0], p2[1] - p1[1])) + 360) % 360
        geo.append({'runway': rw['RWY_ID'], 'nasr_length_ft': int(rw['RWY_LEN']), 'diagram_length_ft': round(length / FT), 'length_diff_m': round(length - int(rw['RWY_LEN']) * FT, 1),
                    'nasr_true_alignment_deg': e1['nasr_true_alignment_deg'], 'nasr_end_to_end_bearing_deg': e1['nasr_end_to_end_bearing_deg'],
                    'diagram_true_bearing_deg': round(brg, 3), 'bearing_diff_deg': round(((brg - e1['nasr_end_to_end_bearing_deg']) + 180) % 360 - 180, 3)})

    # comparison: affine fitted to the NASR runway ends themselves, scored leave-one-out
    loo = []
    for i, r in enumerate(ok_rows):
        train = [x for j, x in enumerate(ok_rows) if j != i]
        try:
            Ti = fit_affine([x['diagram_page_pt'] for x in train], [L.en(x['nasr_lat'], x['nasr_lon']) for x in train])
        except ValueError:   # too few or collinear ends left (comparison only; the graticule fit is unaffected)
            loo.append({'id': r['id'], 'loo_residual_m': None}); continue
        e_, n_ = apply(Ti, r['diagram_page_pt']); te, tn = L.en(r['nasr_lat'], r['nasr_lon'])
        loo.append({'id': r['id'], 'loo_residual_m': round(math.hypot(e_ - te, n_ - tn), 2)})
    Tn = fit_affine([x['diagram_page_pt'] for x in ok_rows], [L.en(x['nasr_lat'], x['nasr_lon']) for x in ok_rows])
    nasr_fit_rms = math.sqrt(sum((lambda p, q: (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2)(apply(Tn, x['diagram_page_pt']), L.en(x['nasr_lat'], x['nasr_lon'])) for x in ok_rows) / len(ok_rows))

    # extra held-out point: helipad H1 (NASR) vs the diagram's H symbol (circle around the 'H' label)
    h1 = None
    hn = next((e for e in ends if e['RWY_END_ID'] == 'H1'), None)
    hl = [l for l in labels if l['text'] == 'H']
    if hn and hl:
        hc = ((hl[0]['bbox'][0] + hl[0]['bbox'][2]) / 2, (hl[0]['bbox'][1] + hl[0]['bbox'][3]) / 2)
        circ = [o for o in objs if o.get('beziers') and o['aspect'] < 1.3 and o['bbox'][0] <= hc[0] <= o['bbox'][2] and o['bbox'][1] <= hc[1] <= o['bbox'][3]]
        if circ:
            c = min(circ, key=lambda o: o['long'])['centre']
            e_, n_ = apply(T, c); te, tn = L.en(float(hn['LAT_DECIMAL']), float(hn['LONG_DECIMAL']))
            h1 = {'diagram_page_pt': [round(v, 3) for v in c], 'residual_m': round(math.hypot(e_ - te, n_ - tn), 2), 'd_east_m': round(e_ - te, 2), 'd_north_m': round(n_ - tn, 2)}

    rv_path = base / 'review.json'; rv = json.loads(rv_path.read_text()); items = {i['id']: i for i in rv['items']}
    thr = items['runway_ends']['thresholds']['max_residual_m']
    worst = max(r['residual_m'] for r in ok_rows)
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    items['runway_ends'].update(status='pass' if len(ok_rows) == len(by_id) and worst <= thr else 'fail',
                                result={'max_residual_m': worst, 'per_end_m': {r['id']: r['residual_m'] for r in ok_rows}, 'checked_at': stamp,
                                        **({'graticule': 'decoded from outlines; this NASR runway-end check is its required independent verification (Franco, Oct 9)'}
                                           if grat_source == 'outline_glyph_decode' else {})})
    diag_ids = sorted({norm_id(l['text']) for l in rl}); nasr_ids = sorted(by_id)
    if not rl:   # nothing printed to compare: a person checks the identifiers
        items['runway_identifiers'].update(status='review', result={'diagram_end_labels': [], 'nasr_ends': nasr_ids, 'nasr_runways': [r['RWY_ID'] for r in rwys],
                                           'note': 'The chart has no runway-end labels in a text layer; ends were identified by the nearest NASR end. Review the identifiers on the chart.', 'checked_at': stamp})
    else:
      items['runway_identifiers'].update(status='pass' if diag_ids == nasr_ids and len([g for g in geo if 'error' not in g]) == len([r for r in rwys if r['RWY_ID'] != 'H1']) else 'fail',
                                       result={'diagram_end_labels': diag_ids, 'nasr_ends': nasr_ids, 'nasr_runways': [r['RWY_ID'] for r in rwys],
                                               'note': 'NASR also lists helipad H1; the diagram labels it H1 (checked separately as a held-out point)' if hn else 'Runways only (NASR lists no helipad H1).', 'checked_at': stamp})
    rv_path.write_text(json.dumps(rv, indent=1, ensure_ascii=False))
    out = {'icao': icao, 'generated_at': stamp, 'pdf_sha256': meta['pdf_sha256'], 'nasr_cycle': json.loads((nasr / 'PROVENANCE.json').read_text())['cycle_effective'],
           'local_frame': {'origin': 'NASR airport reference point', 'lat0': L.lat0, 'lon0': L.lon0, 'datum': 'WGS84', 'projection': 'local equirectangular (east, north) metres'},
           'transform_page_pt_to_local_m': {'coeffs_abcdef': [round(v, 9) for v in T], 'form': 'east = a*x + b*y + c; north = d*x + e*y + f (x right, y down, north-up page pt)',
                                            'fitted_on': 'graticule intersections only', 'graticule_source': grat_source, 'control_points_n': len(ctrl),
                                            'fit_rms_m': round(math.sqrt(sum(c['fit_residual_m'] ** 2 for c in ctrl) / len(ctrl)), 3), 'properties': describe(T)},
           'control_points': ctrl, 'runway_ends': rows, 'runways': geo, 'helipad_H1': h1, **({'nasr_rows_without_position': unplaced} if unplaced else {}),
           'comparison_nasr_fitted': {'fit_rms_m': round(nasr_fit_rms, 2), 'leave_one_out': loo, 'note': 'for comparison only; the reported residuals use the graticule fit'}}
    (base / 'georef.json').write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ('transform_page_pt_to_local_m', 'control_points', 'runway_ends', 'runways', 'helipad_H1', 'comparison_nasr_fitted')}, indent=1))
    print('review:', {k: items[k]['status'] for k in ('runway_ends', 'runway_identifiers')})


def _groups(vals, tol):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return out


if __name__ == '__main__':
    main()
