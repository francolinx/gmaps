"""A4: build the georeferenced map assets for the viewer (ui_scaffold/airport_map.html). Display only. Stdlib only.

  python3 tools/airport_map/build_map.py --icao KLGA

Inputs: build/layers.raw.json (A1), georef.json (A2), provenance.json, osm/osm_aeroways.geojson (A3).
Outputs (production; never contain the debug text layer, tested):
  data/airports/<icao>/map/chart.svg       FAA-chart-derived vector map in local metres (x = east, y = south), north up
  data/airports/<icao>/map/map_meta.json   frame, bounds, layers, provenance footer, OSM attribution, review views
  ui_scaffold/maps/<icao>/                 copies served by the local viewer; OSM in its own osm/ subfolder (ODbL)
Chart layers keep the original FAA styling. Only objects inside the chart frame are drawn: the margin titles and the
cycle-date banners are not (their content is in provenance.json and shown in the viewer footer).
"""
import argparse, json, math, re, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import georef as G   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
LABEL_CLASSES = {'glyph', 'graticule_label'}
NEVER = {'frame', 'inset_frame', 'inset_content'}   # insets are drawn at another scale: never placed on the georeferenced map


def rgb(c):
    return 'none' if c is None else '#%02x%02x%02x' % tuple(max(0, min(255, round(v * 255))) for v in c)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True)
    a = ap.parse_args()
    icao = a.icao.upper(); base = REPO / 'data/airports' / icao.lower()
    raw = json.loads((base / 'build/layers.raw.json').read_text())
    gj = json.loads((base / 'georef.json').read_text()); pv = json.loads((base / 'provenance.json').read_text())
    T = gj['transform_page_pt_to_local_m']['coeffs_abcdef']; ta, tb, tc, td, te, tf = T
    L = G.Local(gj['local_frame']['lat0'], gj['local_frame']['lon0'])
    fx0, fy0, fx1, fy1 = raw['meta']['chart_frame_bbox']
    inside = lambda c: fx0 <= c[0] <= fx1 and fy0 <= c[1] <= fy1
    to_svg = lambda p: (ta * p[0] + tb * p[1] + tc, -(td * p[0] + te * p[1] + tf))   # page pt -> (east, -north) metres
    corners = [to_svg(p) for p in ((fx0, fy0), (fx1, fy0), (fx0, fy1), (fx1, fy1))]
    X0, X1 = min(c[0] for c in corners), max(c[0] for c in corners); Y0, Y1 = min(c[1] for c in corners), max(c[1] for c in corners)
    m_per_pt = math.sqrt(abs(ta * te - tb * td))

    objs = [o for o in raw['objects'] if o['class'] not in NEVER and inside(o['centre'])]
    clips, defs = {}, []
    for o in objs:
        if o['clip'] and o['clip']['d'] not in clips:
            cid = f'clip{len(clips)}'; clips[o['clip']['d']] = cid
            defs.append(f'<clipPath id="{cid}"><path d="{o["clip"]["d"]}" clip-rule="{"evenodd" if o["clip"]["evenodd"] else "nonzero"}"/></clipPath>')

    def path(o):
        f = rgb(o['fill_rgb']) if o['fill'] else 'none'; s = rgb(o['stroke_rgb']) if o['stroke'] else 'none'
        layer = 'L-labels' if o['class'] in LABEL_CLASSES else 'L-chart'
        at = [f'class="{layer}"', f'd="{o["d"]}"', f'fill="{f}"', f'fill-rule="{"evenodd" if o["evenodd"] else "nonzero"}"', f'data-class="{o["class"]}"']
        if s == 'none' and o['class'] == 'pavement':   # map only: hide anti-aliasing seams between adjacent pavement fills
            at += [f'stroke="{f}"', 'stroke-width="0.25"', 'stroke-linejoin="round"']
        if s != 'none':
            at += [f'stroke="{s}"', f'stroke-width="{max(o["linewidth"], 0.1):.3f}"', 'stroke-linejoin="round"']
            if o['dash']:
                at.append(f'stroke-dasharray="{" ".join(f"{v:.3f}" for v in o["dash"][0])}" stroke-dashoffset="{o["dash"][1]:.3f}"')
        if o['clip']:
            at.append(f'clip-path="url(#{clips[o["clip"]["d"]]})"')
        return '<path ' + ' '.join(at) + '/>'

    esc = lambda t: t.replace('&', '&amp;').replace('<', '&lt;')
    M = f'matrix({ta:.9f} {-td:.9f} {tb:.9f} {-te:.9f} {tc:.6f} {-tf:.6f})'   # page pt -> metres east / south
    drawn = [path(o) for o in objs]   # ONE group in original paint order (white insets stay on top of their black boxes)
    n_lab = sum(1 for o in objs if o['class'] in LABEL_CLASSES)
    vis = [l for l in raw['labels'] if l.get('visible_text') and l['class'] != 'inset_label' and inside(((l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2))]
    texts = [f'<text class="L-labels" x="{l["x"]}" y="{l["y"]}" font-size="{l["size"]}" font-family="Helvetica,Arial,sans-serif" fill="{rgb(l["colour"]) if l["colour"] else "#000"}" '
            f'textLength="{l["run"]}" lengthAdjust="spacingAndGlyphs" transform="rotate({l["angle"]} {l["x"]} {l["y"]})">{esc(l["text"])}</text>' for l in vis]
    svg = '\n'.join([
        f'<svg xmlns="http://www.w3.org/2000/svg" id="chart-svg" viewBox="{X0:.2f} {Y0:.2f} {X1 - X0:.2f} {Y1 - Y0:.2f}" preserveAspectRatio="xMidYMid meet">',
        f'<!-- Reconstructed from FAA airport diagram, cycle {pv["chart"]["cycle"]} (public domain). Display only. Not for navigation. No predictions. '
        f'Units: metres east (x) / south (y) of the NASR airport reference point. -->',
        f'<defs>{"".join(defs)}</defs>',
        '<style>svg.hide-chart .L-chart{display:none} svg.hide-labels .L-labels{display:none}</style>',
        f'<g id="layer-chart-and-labels" transform="{M}">', *drawn, *texts, '</g>',
        '<g id="layer-osm"></g>',
        '</svg>'])

    def view(cx, cy, half):   # metres box centred on a page-pt point
        x, y = to_svg((cx, cy)); return [round(x - half, 1), round(y - half, 1), round(2 * half, 1), round(2 * half, 1)]
    # review views, derived from the data (no airport-specific landmarks)
    labs = raw['labels']; cen = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    slug = lambda t: re.sub(r'[^a-z0-9]+', '', t.lower())
    views = {'full': [round(X0, 1), round(Y0, 1), round(X1 - X0, 1), round(Y1 - Y0, 1)]}
    rws = [o for o in raw['objects'] if o['class'] == 'runway']
    for i, a_ in enumerate(rws):          # runway intersections (within both runways)
        for b_ in rws[i + 1:]:
            (p1, u1), (p2, u2) = (a_['centre'], a_['u']), (b_['centre'], b_['u'])
            den = u1[0] * u2[1] - u1[1] * u2[0]
            if abs(den) < 1e-6:
                continue
            t1 = ((p2[0] - p1[0]) * u2[1] - (p2[1] - p1[1]) * u2[0]) / den; t2 = ((p2[0] - p1[0]) * u1[1] - (p2[1] - p1[1]) * u1[0]) / den
            if abs(t1) <= a_['long'] / 2 and abs(t2) <= b_['long'] / 2:
                views.setdefault('runway_intersection', view(p1[0] + t1 * u1[0], p1[1] + t1 * u1[1], 260))
    ends = [(r['id'], r['diagram_page_pt']) for r in gj['runway_ends'] if 'diagram_page_pt' in r]
    groups = {}                            # runway-end hold areas: ends with the same runway number (10L/10R) share one view
    for eid, pt in ends:
        groups.setdefault(re.sub(r'[LRC]$', '', eid), []).append((eid, pt))
    groups = list(groups.values())
    for g in groups:
        c = (sum(q[0] for _, q in g) / len(g), sum(q[1] for _, q in g) / len(g))
        half = max(250.0, max(math.dist(to_svg(c), to_svg(q)) for _, q in g) + 220.0)
        views['runway_ends_' + '_'.join(sorted(e for e, _ in g))] = view(*c, half)
    for l in labs:                         # terminals
        if l['class'] == 'terminal_label':
            t = min((x for x in labs if x['text'] == 'TERMINAL'), key=lambda x: math.dist(cen(x['bbox']), cen(l['bbox'])), default=l)
            views.setdefault('terminal_' + slug(l['text']), view(*cen(t['bbox']), 420))
    shapes = [o for o in raw['objects'] if o['class'] == 'hot_spot' and o['stroke'] and not o['fill'] and o.get('beziers') and (o['long'] >= 10 and o['aspect'] <= 6 or o['long'] >= 4 and o['aspect'] <= 1.5)]
    for l in labs:                         # hot spots: the outline nearest each callout
        if l['class'] == 'hot_spot_label' and shapes:
            o = min(shapes, key=lambda o: math.dist(o['centre'], cen(l['bbox'])))
            views.setdefault('hot_spot_' + slug(l['text']), view(*o['centre'], max(220.0, o['long'] * m_per_pt)))
    # from OSM taxiways + NASR runway ends (display review only): end-around taxiways and the busiest runway-crossing area
    osm_p = base / 'osm/osm_aeroways.geojson'
    if osm_p.exists():
        import csv
        en_svg = lambda lat, lon: (lambda e, n: (e, -n))(*L.en(lat, lon))
        with open(base / 'sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8') as f:
            nends = {}
            for r in csv.DictReader(f):
                if r['LAT_DECIMAL'].strip() and not r['RWY_END_ID'].startswith('H'):
                    nends.setdefault(r['RWY_ID'], []).append((r['RWY_END_ID'], en_svg(float(r['LAT_DECIMAL']), float(r['LONG_DECIMAL']))))
        runways = [v for v in nends.values() if len(v) == 2]
        twys = [f for f in json.loads(osm_p.read_text())['features'] if f['properties'].get('aeroway') == 'taxiway' and f['geometry']['type'] == 'LineString']
        segs = lambda f: [(en_svg(a[1], a[0]), en_svg(b[1], b[0])) for a, b in zip(f['geometry']['coordinates'], f['geometry']['coordinates'][1:])]
        def cross(p1, p2, q1, q2):   # intersection point of two segments, or None
            d = (p2[0] - p1[0]) * (q2[1] - q1[1]) - (p2[1] - p1[1]) * (q2[0] - q1[0])
            if abs(d) < 1e-9:
                return None
            t = ((q1[0] - p1[0]) * (q2[1] - q1[1]) - (q1[1] - p1[1]) * (q2[0] - q1[0])) / d; u = ((q1[0] - p1[0]) * (p2[1] - p1[1]) - (q1[1] - p1[1]) * (p2[0] - p1[0])) / d
            return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])) if 0 <= t <= 1 and 0 <= u <= 1 else None
        crossings, end_around = [], {}
        for f in twys:
            ss = segs(f)
            for (e1, a), (e2, b) in runways:
                on_rwy = [x for s1, s2 in ss if (x := cross(s1, s2, a, b))]
                crossings += on_rwy
                if on_rwy:
                    continue
                L_ = math.dist(a, b); u = ((b[0] - a[0]) / L_, (b[1] - a[1]) / L_)
                for eid, endp, sgn in ((e1, a, -1), (e2, b, 1)):   # extended centreline 250-1500 m beyond this end
                    far = (endp[0] + sgn * u[0] * 1500, endp[1] + sgn * u[1] * 1500); near_ = (endp[0] + sgn * u[0] * 250, endp[1] + sgn * u[1] * 250)
                    hit = next((x for s1, s2 in ss if (x := cross(s1, s2, near_, far))), None)
                    if hit:
                        ref = f['properties'].get('ref') or 'unnamed'
                        end_around.setdefault(f'end_around_{slug(ref)}_{eid}', hit)
        for k, (x, y) in sorted(end_around.items()):
            views[k] = [round(x - 600, 1), round(y - 600, 1), 1200.0, 1200.0]
        if crossings:
            best = max(crossings, key=lambda c: sum(math.dist(c, o) <= 400 for o in crossings))
            views['busiest_runway_crossings'] = [round(best[0] - 500, 1), round(best[1] - 500, 1), 1000.0, 1000.0]
    rep = json.loads((base / 'review/extract_report.json').read_text())
    for k, g in enumerate(rep['hold_line_groups']):   # ILS hold lines ('ils_hold_lines' = the first, as before)
        views['ils_hold_lines' if k == 0 else f'ils_hold_line_{k + 1}'] = view(*cen(g['bbox']), 110)

    meta = {'icao': icao, 'units': 'metres; x = east, y = south (SVG), origin = NASR airport reference point',
            'frame': {'lat0': L.lat0, 'lon0': L.lon0, 'M': L.M, 'N': L.N, 'projection': 'local equirectangular, WGS84'},
            'bounds_m': {'x0': X0, 'y0': Y0, 'x1': X1, 'y1': Y1}, 'chart_m_per_pt': round(m_per_pt, 4),
            'layers': [{'id': 'layer-chart', 'css_class': 'L-chart', 'label': 'Chart linework', 'default': True},
                       {'id': 'layer-labels', 'css_class': 'L-labels', 'label': 'Chart labels', 'default': True},
                       {'id': 'layer-osm', 'label': 'OSM taxiways', 'default': False, 'requires_attribution': True, 'src': 'osm/osm_taxiways.geojson'}],
            'footer': pv['display']['footer'], 'chart': {k: pv['chart'][k] for k in ('cycle', 'effective_from', 'effective_to', 'sha256', 'url', 'chart_code')},
            'osm_attribution': pv['osm']['attribution'] if pv.get('osm') else None, 'osm_data_timestamp': pv['osm']['osm_data_timestamp'] if pv.get('osm') else None,
            'georef': {'runway_end_residual_m': {r['id']: r['residual_m'] for r in gj['runway_ends'] if 'residual_m' in r}, 'helipad_H1_m': (gj.get('helipad_H1') or {}).get('residual_m')},
            'review_views': views,
            'sign_off': (json.loads((base / 'review.json').read_text()).get('sign_off') if (base / 'review.json').exists() else None),
            'notice': 'Display only. Not for navigation. No predictions. Chart-derived geometry is not the geometry of record (review.json).'}
    draft = (json.loads((base / 'review.json').read_text()).get('draft') if (base / 'review.json').exists() else None)
    if draft:   # draft class (Franco, Oct 18): display only, not reviewed; the viewer refuses ledger overlays. Absent on every reviewed map.
        meta.update(status=draft['status'], overlays_allowed=draft['overlays_allowed'], draft_reason=draft['draft_reason'])
    out = base / 'map'; out.mkdir(exist_ok=True)
    (out / 'chart.svg').write_text(svg); (out / 'map_meta.json').write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    ui = REPO / 'ui_scaffold/maps' / icao.lower(); (ui / 'osm').mkdir(parents=True, exist_ok=True)
    shutil.copy(out / 'chart.svg', ui / 'chart.svg'); shutil.copy(out / 'map_meta.json', ui / 'map_meta.json')
    if (base / 'osm/osm_aeroways.geojson').exists():   # OSM: taxiway/taxilane lines only, in its own folder, ODbL
        fc = json.loads((base / 'osm/osm_aeroways.geojson').read_text())
        fc['features'] = [f for f in fc['features'] if f['properties']['aeroway'] in ('taxiway', 'taxilane') and f['geometry']['type'] == 'LineString']
        vp = base / 'review/taxiway_vocabulary.json'   # Franco R1/R6: OSM never adds or renames a name on our map
        if vp.exists():
            v = json.loads(vp.read_text()); ok = set(v['taxiways']) | {str(g) for g in v['gates_destination_only']}
            for f in fc['features']:
                pr = f['properties']
                if pr.get('ref') and pr['ref'] not in ok:
                    pr['ref_hidden'] = 'not a name on the FAA chart (Franco R1/R6): geometry only'
                    pr['ref'] = None; pr['name'] = None; pr['tags'] = {k: val for k, val in pr.get('tags', {}).items() if k not in ('ref', 'name')}
        (ui / 'osm/osm_taxiways.geojson').write_text(json.dumps(fc))
        shutil.copy(base / 'osm/ATTRIBUTION.md', ui / 'osm/ATTRIBUTION.md')
    print(f'chart.svg: {len(drawn) - n_lab} linework + {n_lab + len(texts)} label elements (one paint-ordered group); bounds {X1 - X0:.0f} x {Y1 - Y0:.0f} m; views {list(views)}')


if __name__ == '__main__':
    main()
