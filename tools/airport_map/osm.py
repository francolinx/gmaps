"""A3: OpenStreetMap aeroway layer + taxiway-name cross-check against the FAA chart labels. Display/review only.

  python3 tools/airport_map/osm.py fetch --icao KLGA              # Overpass query -> data/airports/<icao>/osm/ (raw + provenance)
  python3 tools/airport_map/osm.py import --icao KLGA --raw F --query Q --endpoint URL --requested-at ISO   # same, from a saved response
  python3 tools/airport_map/osm.py build --icao KLGA              # osm_aeroways.geojson + name cross-check report

OSM data is © OpenStreetMap contributors, ODbL 1.0. It stays in data/airports/<icao>/osm/ with its own ATTRIBUTION.md and
PROVENANCE.json (query, endpoint, request time, OSM data timestamp, sha256) and is never merged into the chart-derived
layers. The cross-check REPORTS differences (matched / chart-only / OSM-only / conflicting) and resolves nothing.
Stdlib only.
"""
import argparse, collections, hashlib, json, math, re, sys, time, urllib.parse, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import georef as G   # noqa: E402  (Local frame + transform helpers)

REPO = Path(__file__).resolve().parents[2]
ATTRIBUTION = '© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)'
ENDPOINTS = ('https://overpass-api.de/api/interpreter', 'https://overpass.private.coffee/api/interpreter')
QUERY = """[out:json][timeout:90][bbox:{s},{w},{n},{e}];
(
  way["aeroway"~"^(taxiway|taxilane|runway|holding_position|apron|parking_position|stopway)$"];
  node["aeroway"~"^(holding_position|runway_end)$"];
);
out body geom;
"""
MATCH_M = 60.0       # default; per airport: match_radius() (Franco R2, ~3 mm on paper at the chart scale)
MARGIN_DEG = 0.012   # bbox margin around the NASR runway ends



def gate_note(n):
    """One sentence on numbered gate lead-ins, from the data (never a route element)."""
    g = n['chart_gates']
    if not g:
        return 'No numbered gate lead-ins on this chart. '
    co = ''.join(f'#{x} chart-only; ' for x in n['chart_only'])
    return f'Numbers {min(g)}-{max(g)} are gate lead-ins (Franco, Oct 8), not taxiway names: destination only, never a route element; {co}'.rstrip('; ') + '. '

def bbox(base):
    import csv
    with open(base / 'sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8') as f:
        pts = [(float(r['LAT_DECIMAL']), float(r['LONG_DECIMAL'])) for r in csv.DictReader(f) if r['LAT_DECIMAL'].strip() and r['LONG_DECIMAL'].strip()]
    return (round(min(p[0] for p in pts) - MARGIN_DEG, 4), round(min(p[1] for p in pts) - MARGIN_DEG, 4),
            round(max(p[0] for p in pts) + MARGIN_DEG, 4), round(max(p[1] for p in pts) + MARGIN_DEG, 4))


def save(base, raw_bytes, query, endpoint, requested_at):
    d = json.loads(raw_bytes)
    if not d.get('elements'):
        raise SystemExit('Overpass response has no elements; not saving')
    o = base / 'osm'; o.mkdir(parents=True, exist_ok=True)
    (o / 'overpass_raw.json').write_bytes(raw_bytes)
    (o / 'query.overpassql').write_text(query)
    prov = {'source': 'OpenStreetMap via Overpass API', 'licence': 'ODbL 1.0', 'attribution': ATTRIBUTION, 'endpoint': endpoint,
            'requested_at': requested_at, 'osm_data_timestamp': (d.get('osm3s') or {}).get('timestamp_osm_base'),
            'generator': d.get('generator'), 'query_file': 'query.overpassql', 'raw_file': 'overpass_raw.json',
            'raw_sha256': hashlib.sha256(raw_bytes).hexdigest(), 'elements': len(d['elements'])}
    (o / 'PROVENANCE.json').write_text(json.dumps(prov, indent=1))
    (o / 'ATTRIBUTION.md').write_text(
        '# OpenStreetMap data in this folder\n\n'
        f'{ATTRIBUTION}\n\n'
        'Everything in this folder (`overpass_raw.json`, `osm_aeroways.geojson`, and any file derived from them) is derived from '
        'OpenStreetMap data and is licensed under the Open Database License (ODbL) 1.0: https://opendatacommons.org/licenses/odbl/1-0/ .\n\n'
        'It is kept separate from the FAA-chart-derived layers (US Government work, public domain), which are not combined with it. '
        'Any map view that shows this layer must show the attribution above.\n')
    print(json.dumps(prov, indent=1))


OVERPASS_STAMP = Path.home() / '.cache/gmaps/overpass_last_query'
MIN_GAP_S = 30   # at most one Overpass query per 30 s, across runs


def overpass_wait():
    try:
        last = float(OVERPASS_STAMP.read_text())
    except (OSError, ValueError):
        last = 0
    time.sleep(max(0, last + MIN_GAP_S - time.time()))
    OVERPASS_STAMP.parent.mkdir(parents=True, exist_ok=True); OVERPASS_STAMP.write_text(str(time.time()))


def fetch(base):
    s, w, n, e = bbox(base)
    q = QUERY.format(s=s, w=w, n=n, e=e)
    last, backoff = None, 60
    for attempt in range(4):
        for ep in ENDPOINTS:
            overpass_wait()
            try:
                req = urllib.request.Request(ep, data=urllib.parse.urlencode({'data': q}).encode(), headers={'User-Agent': 'gmaps-airport-map/0.1 (local research)'})
                with urllib.request.urlopen(req, timeout=180) as r:
                    raw = r.read()
                save(base, raw, q, ep, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
                return
            except Exception as ex:   # busy public servers: back off (429 / 504 / timeouts), then try the next one
                last = f'{ep}: {type(ex).__name__} {getattr(ex, "code", "")}'
                print('  ', last, f'(backing off {backoff} s)', file=sys.stderr)
                time.sleep(backoff); backoff = min(backoff * 2, 600)
    raise SystemExit(f'Overpass unavailable ({last})')


def seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]; L2 = vx * vx + vy * vy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L2))
    return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)


def norm_ref(r):
    return re.sub(r'\s+', '', (r or '').upper())


def match_radius(gj):
    """Franco R2: ~3 mm on paper at the chart's scale, rounded to 5 m (KLGA 1:20,485 -> 60 m, unchanged)."""
    sc = (gj['transform_page_pt_to_local_m'].get('properties') or {}).get('chart_scale_1_to')
    return float(round(0.003 * sc / 5) * 5) if sc else 60.0


R6_RAMP = re.compile(r"(.*/.*|\d.*|[A-Z][A-Z' .]{3,})")   # slash alleys (B/C), digit-first codes (1W, 5C, 103A), words (EAST, INNER, HEAVY, INT'L)


def r6_class(ref, chart_tokens):
    """Franco R6: an OSM-only ref is (a) an extraction/matching miss if the string is anywhere in the chart text, else
    (b) ramp / non-movement, or (c) an uncharted OSM name. (b) and (c) are ignored: never displayed, never in the parser."""
    if R6_RAMP.fullmatch(ref) or ref in ('UAL',):   # digit-first codes, slash alleys and words are ramp labels even where the chart prints them
        return 'ramp_or_non_movement' + (' (printed on the chart as a ramp/area label)' if ref in chart_tokens else '')
    if ref in chart_tokens:
        return 'chart_text_has_it'
    return 'uncharted_osm_name'


def build(base):
    o = base / 'osm'
    raw = json.loads((o / 'overpass_raw.json').read_text()); prov = json.loads((o / 'PROVENANCE.json').read_text())
    gj = json.loads((base / 'georef.json').read_text())
    L = G.Local(gj['local_frame']['lat0'], gj['local_frame']['lon0']); T = gj['transform_page_pt_to_local_m']['coeffs_abcdef']
    layers = json.loads((base / 'build/layers.raw.json').read_text())
    MATCH_M = match_radius(gj)

    # 1. GeoJSON (ODbL), one feature per OSM element, tags kept as-is
    feats = []
    for el in raw['elements']:
        tags = el.get('tags', {}); aw = tags.get('aeroway')
        if el['type'] == 'node':
            geom = {'type': 'Point', 'coordinates': [el['lon'], el['lat']]}
        else:
            coords = [[p['lon'], p['lat']] for p in el.get('geometry', [])]
            closed = len(coords) > 3 and coords[0] == coords[-1]
            geom = {'type': 'Polygon', 'coordinates': [coords]} if closed and aw in ('apron',) else {'type': 'LineString', 'coordinates': coords}
        feats.append({'type': 'Feature', 'geometry': geom, 'properties': {'osm_type': el['type'], 'osm_id': el['id'], 'aeroway': aw,
                                                                       'ref': tags.get('ref'), 'name': tags.get('name'), 'tags': tags}})
    fc = {'type': 'FeatureCollection', 'attribution': ATTRIBUTION, 'licence': 'ODbL 1.0', 'osm_data_timestamp': prov['osm_data_timestamp'],
          'note': 'OpenStreetMap-derived; kept separate from FAA-chart-derived layers. Display/review only.', 'features': feats}
    (o / 'osm_aeroways.geojson').write_text(json.dumps(fc))

    # 2. ways in local metres
    ways = []
    for el in raw['elements']:
        tags = el.get('tags', {})
        if el['type'] == 'way' and tags.get('aeroway') in ('taxiway', 'taxilane'):
            ways.append({'id': el['id'], 'ref': norm_ref(tags.get('ref')), 'aeroway': tags['aeroway'], 'pts': [L.en(p['lat'], p['lon']) for p in el['geometry']]})
    dist = lambda pt, w: min(seg_dist(pt, w['pts'][i], w['pts'][i + 1]) for i in range(len(w['pts']) - 1)) if len(w['pts']) > 1 else 1e9

    # 3. chart taxiway labels -> ground position; labels inside a circle symbol are chart callouts (A1/A5 approach
    #    lights, H helipad, circled P), not taxiway names
    # chart callouts: a letter inside a small circle (A1/A5 approach lights, H helipad, circled P) or on a small filled
    # symbol badge (e.g. the solid L = pilot-controlled lighting); these are legend symbols, not taxiway names
    circles = [ob for ob in layers['objects'] if (ob.get('beziers') and ob['aspect'] < 1.3 and ob['long'] < 15)
               or (ob['class'] == 'symbol' and ob['fill'] and ob['long'] < 15)]
    inside = lambda c, ob: ob['bbox'][0] <= c[0] <= ob['bbox'][2] and ob['bbox'][1] <= c[1] <= ob['bbox'][3]
    inst, callouts, seen_at, dupes = [], [], [], 0
    hn_path = base / 'review/human_notes.json'
    hn = json.loads(hn_path.read_text()) if hn_path.exists() else {}
    pending = (hn.get('taxiway_names_pending_crop') or {}).get('rows', [])
    decided = (hn.get('taxiway_names_decisions') or {}).get('rows', [])   # Franco's per-row decisions after the crops
    for l in layers['labels']:
        if l['class'] != 'taxiway_candidate':
            continue
        c = ((l['bbox'][0] + l['bbox'][2]) / 2, (l['bbox'][1] + l['bbox'][3]) / 2)
        if any(inside(c, ob) for ob in circles):
            callouts.append(l['text']); continue
        en = G.apply(T, c)
        if any(t == l['text'] and math.dist(en, q) <= 2.0 for t, q in seen_at):   # Franco R7: the same label drawn twice at one spot
            dupes += 1; continue
        seen_at.append((l['text'], en))
        near = sorted(((dist(en, w), w) for w in ways), key=lambda t: t[0])
        within = [(d, w) for d, w in near if d <= MATCH_M]
        same = [d for d, w in within if w['ref'] == norm_ref(l['text'])]
        lat, lon = L.ll(*en)
        rec = {'label': l['text'], 'chart_page_pt': [round(c[0], 2), round(c[1], 2)], 'lat': round(lat, 7), 'lon': round(lon, 7),
               'nearest_osm': [{'ref': w['ref'] or None, 'osm_id': w['id'], 'dist_m': round(d, 1)} for d, w in near[:3]]}
        if same:
            rec.update(status='match', dist_m=round(min(same), 1))
        elif within:
            rec.update(status='conflict', nearby_refs=sorted({w['ref'] or '<no ref>' for _, w in within}))
        else:
            rec.update(status='no_osm_taxiway_within_%dm' % MATCH_M)
        if rec['status'] != 'match':   # Franco's decisions: the FAA chart is authoritative for names (R1)
            crop = next(((k, p) for k, p in enumerate(pending, 1) if p['label'] == l['text'] and math.dist(L.en(p['lat'], p['lon']), en) <= 30), None)
            fd = next((p for p in decided if p['label'] == l['text'] and math.dist(L.en(p['lat'], p['lon']), en) <= 30), None)
            if fd:
                rec.update(decision=f"{fd['decision']} (Franco, after the crop)", rule=fd['rule'], decided_by='Franco')
            elif crop:
                rec.update(decision='pending: crop for Franco', rule='crop', question=crop[1]['question'], crop_file=f"docs/name_crops/{base.name}_{l['text']}_pending_{crop[0]}.png")
            elif within and not within[0][1]['ref']:
                rec.update(decision='chart right; OSM way unnamed', rule='R4', osm_unnamed_way=within[0][1]['id'])
            elif within:
                rec.update(decision=f"chart right (OSM's nearest ref here is {within[0][1]['ref']})", rule='R5')
            else:
                rec.update(decision='chart right; no OSM way within the radius (OSM geometry missing or offset here)', rule='R1')
        inst.append(rec)
    chart_names = sorted({r['label'] for r in inst})
    osm_names = sorted({w['ref'] for w in ways if w['ref'] and not w['ref'].isdigit()})
    # numbered refs = gate lead-ins: chart class gate_leadin keyed by gate number; NOT taxiway names.
    # Clearance parsing may use them only as a destination ("to gate 14"), never as a route element. When the graph is
    # rebuilt from reviewed OSM, the matching OSM ways may become gate-access edges labelled by gate number.
    chart_nums = sorted({l['gate'] for l in layers['labels'] if l['class'] == 'gate_leadin'})
    osm_nums = collections.Counter(int(w['ref']) for w in ways if w['ref'].isdigit())
    # human review notes are airport data (review/human_notes.json), never code
    hn_path = base / 'review/human_notes.json'
    hn = json.loads(hn_path.read_text()) if hn_path.exists() else {}
    numbered = {'status': 'resolved_gate_leadin' if chart_nums else 'none_on_chart', 'decided_by': 'Franco', 'decided_at': '2026-10-08', 'chart_class': 'gate_leadin (keyed by gate number)',
                'chart_gates': chart_nums, 'osm_ways_by_gate': {str(k): osm_nums[k] for k in sorted(osm_nums)},
                'osm_aeroway': dict(collections.Counter(w['aeroway'] for w in ways if w['ref'].isdigit())),
                'chart_only': sorted(set(chart_nums) - set(osm_nums)), 'osm_only': sorted(set(osm_nums) - set(chart_nums)),
                'chart_only_note': {str(g): hn.get('gate_leadin_chart_only', {}).get(str(g), f'chart-only: no OSM taxiway/taxilane with ref {g}. Not reviewed yet.')
                                    for g in sorted(set(chart_nums) - set(osm_nums))},
                'use': 'Destination only in clearance parsing ("to gate 14"), never a route element. Future graph: matching OSM ways -> gate-access edges labelled by gate number.'}
    by = collections.defaultdict(list)
    for r in inst:
        by[r['label']].append(r['status'])
    summary = {'matched': [n for n in chart_names if n in osm_names and all(s == 'match' for s in by[n])],
               'matched_with_exceptions': [n for n in chart_names if n in osm_names and 'match' in by[n] and any(s != 'match' for s in by[n])],
               'conflicting': [n for n in chart_names if n in osm_names and 'match' not in by[n]],
               'chart_only': [n for n in chart_names if n not in osm_names],
               'osm_only': [n for n in osm_names if n not in chart_names]}

    # Franco R6: classify OSM-only refs; R1: the parser vocabulary is chart names only
    import string
    toks = set()
    for l in layers['labels']:
        toks |= {t.strip(string.punctuation) for t in re.split(r'[\s,;:()]+', l['text'])} | {l['text']}
    osm_only_class = {ref: r6_class(ref, toks) for ref in summary['osm_only']}
    vocab = {'icao': base.name.upper(), 'rule': 'Franco R1 (Oct 9): the current FAA chart is authoritative for names; OSM gives geometry only and never adds or renames a taxiway. Clearance-parser vocabulary = chart names only.',
             'taxiways': chart_names, 'gates_destination_only': chart_nums, 'never': sorted(summary['osm_only'])}
    (base / 'review/taxiway_vocabulary.json').write_text(json.dumps(vocab, indent=1) + '\n')

    # 4. OSM runway ends vs NASR (information for "geometry of record" later)
    import csv
    with open(base / 'sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8') as f:
        nasr = {r['RWY_END_ID'].lstrip('0'): L.en(float(r['LAT_DECIMAL']), float(r['LONG_DECIMAL'])) for r in csv.DictReader(f) if r['RWY_END_ID'] != 'H1' and r['LAT_DECIMAL'].strip() and r['LONG_DECIMAL'].strip()}
    rw_rows = []
    for el in raw['elements']:
        tags = el.get('tags', {})
        if el['type'] == 'way' and tags.get('aeroway') == 'runway' and tags.get('ref'):
            ends = [x.lstrip('0') for x in tags['ref'].split('/')]
            pts = [L.en(p['lat'], p['lon']) for p in el['geometry']]
            for e_id in ends:
                if e_id in nasr:
                    d = min(math.hypot(p[0] - nasr[e_id][0], p[1] - nasr[e_id][1]) for p in (pts[0], pts[-1]))
                    rw_rows.append({'end': e_id.zfill(2), 'osm_way': el['id'], 'osm_endpoint_to_nasr_end_m': round(d, 2)})

    rep = {'icao': base.name.upper(), 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'osm': {k: prov[k] for k in ('endpoint', 'requested_at', 'osm_data_timestamp', 'raw_sha256', 'attribution')},
           'method': f'chart label centre -> ground via georef.json (graticule fit); match if an OSM taxiway/taxilane with the same ref is within {MATCH_M:.0f} m of it',
           **({'match_radius_rule': f'Franco R2: ~3 mm on paper at the chart scale -> {MATCH_M:.0f} m', 'duplicate_labels_dropped': dupes, 'osm_only_classified': osm_only_class} if (dupes or osm_only_class or MATCH_M != 60.0) else {}),
           'osm_taxiway_ways': len(ways), 'osm_ways_without_ref': sum(1 for w in ways if not w['ref']),
           'chart_label_instances': len(inst), 'chart_symbol_callouts_excluded': sorted(collections.Counter(callouts).items()),
           'summary': summary, 'gate_leadins': numbered, 'review_list': [r for r in inst if r['status'] != 'match'], 'instances': inst,
           'osm_runway_ends_vs_nasr': rw_rows}
    (base / 'review/osm_names_report.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False))

    # 5. checklist + provenance
    rv_path = base / 'review.json'; rv = json.loads(rv_path.read_text())
    item = next(i for i in rv['items'] if i['id'] == 'taxiway_names')
    letters_ok = not summary['conflicting'] and not summary['chart_only'] and not summary['osm_only'] and not rep['review_list']
    # with Franco's rules every row is decided except crop rows and R6 'chart text has it' (our miss, to be fixed)
    undecided = [r for r in rep['review_list'] if r.get('rule') in (None, 'crop')] + [k for k, v in osm_only_class.items() if v == 'chart_text_has_it']
    if not chart_names:   # Franco R9: a chart without text labels (KPDK) is not decided until its outline text is decoded
        undecided.append('R9: no chart labels yet')
    decided = not letters_ok and not undecided
    item.update(status='pass' if letters_ok or decided else 'review', result={k: v for k, v in summary.items()} | ({'decided_by_rules': {r: sum(1 for x in rep['review_list'] if x.get('rule') == r) for r in ('R1', 'R4', 'R5')} | {'R6_ignored': sum(1 for v in osm_only_class.values() if v != 'chart_text_has_it')}, 'undecided': [str(u if isinstance(u, str) else u['label']) for u in undecided]} if not letters_ok else {}) | {'review_items': len(rep['review_list']), 'letter_names_ok': letters_ok,
                'gate_leadins': {'chart_only': numbered['chart_only'], 'osm_only': numbered['osm_only'], 'status': numbered['status']}, 'checked_at': rep['generated_at']},
                note=('Letter-named taxiways: ' + ('all matched. ' if letters_ok else 'every difference decided by Franco\'s rules R1-R7 (Oct 9; docs/TAXIWAY_NAMES_REVIEW.md). ' if decided else 'differences listed in docs/TAXIWAY_NAMES_REVIEW.md. ') +
                      gate_note(numbered) + 'Nothing auto-resolved.'))
    rv_path.write_text(json.dumps(rv, indent=1, ensure_ascii=False))
    pp = base / 'provenance.json'
    if pp.exists():
        pv = json.loads(pp.read_text()); pv['osm'] = {k: prov[k] for k in ('source', 'licence', 'attribution', 'endpoint', 'requested_at', 'osm_data_timestamp', 'raw_sha256')} | {'folder': 'osm/'}
        pp.write_text(json.dumps(pv, indent=1, ensure_ascii=False))
    print(json.dumps({k: rep[k] for k in ('osm_taxiway_ways', 'osm_ways_without_ref', 'chart_label_instances', 'chart_symbol_callouts_excluded', 'summary', 'gate_leadins')}, indent=1, ensure_ascii=False))
    print('review list:'); [print('  ', r['label'], r['status'], r.get('nearby_refs', ''), r['nearest_osm'][:2]) for r in rep['review_list']]
    print('checklist taxiway_names:', item['status'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('fetch', 'import', 'build')); ap.add_argument('--icao', required=True)
    ap.add_argument('--raw'); ap.add_argument('--endpoint'); ap.add_argument('--requested-at'); ap.add_argument('--query', help='the exact query file that was sent')
    a = ap.parse_args()
    base = REPO / 'data/airports' / a.icao.lower()
    if a.cmd == 'fetch':
        fetch(base)
    elif a.cmd == 'import':
        if not (a.raw and a.endpoint and a.requested_at and a.query):
            raise SystemExit('import needs --raw --endpoint --requested-at --query (the exact query sent)')
        save(base, Path(a.raw).read_bytes(), Path(a.query).read_text(), a.endpoint, a.requested_at)
    else:
        build(base)


if __name__ == '__main__':
    main()
