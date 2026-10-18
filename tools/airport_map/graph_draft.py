"""KLGA-style prediction-geometry DRAFT. NOT WIRED TO ANYTHING: no runtime code may load
it (tests/run_all.py enforces this). Predictions stay off; nothing here is reviewed.

  .venv/bin/python tools/airport_map/graph_draft.py --icao KLGA

Builds data/airports/<icao>/graph_draft/graph_draft.json from
  - OSM taxiway / taxilane / runway ways (ODbL; the draft is a derivative database, see ATTRIBUTION.md there),
  - NASR runway ends (geometry of record for runway ends),
  - hold-short nodes: OSM holding_position nodes and ways, plus the chart's ILS hold lines (georeferenced, display grade),
  - gate lead-in edges: OSM ways whose ref is a gate number on the chart (destination only, never a route element),
and writes GRAPH_DRAFT_REVIEW.md: a diff against the hand-built v1 graph data/airports/<icao>.graph.json.
"""
import argparse, collections, csv, json, math, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools/airport_map'))
import georef as G   # noqa: E402  (Local frame)

MOVE = ('taxiway', 'taxilane', 'runway')
SNAP_HOLD_WAY_M = 20.0    # an OSM holding_position way that shares no node with a taxiway: nearest taxiway node within this
SNAP_CHART_HOLD_M = 40.0  # a chart ILS hold line (display-grade georef, ~10 m) to the nearest draft node within this
SNAP_NASR_END_M = 60.0    # a NASR runway end to the nearest OSM runway-way node within this


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True)
    a = ap.parse_args(); icao = a.icao.upper(); base = REPO / 'data/airports' / icao.lower(); out = base / 'graph_draft'
    osm = json.loads((base / 'osm/overpass_raw.json').read_text())
    gj = json.loads((base / 'georef.json').read_text())
    L = G.Local(gj['local_frame']['lat0'], gj['local_frame']['lon0'])
    dist = lambda p, q: math.hypot(*(x - y for x, y in zip(L.en(*p), L.en(*q))))
    gates = set(json.loads((base / 'review/osm_names_report.json').read_text()).get('gate_leadins', {}).get('chart_gates', []))

    # ---- OSM ways and nodes ----
    pos, ways = {}, []
    for el in osm['elements']:
        if el['type'] == 'node':
            pos[el['id']] = (el['lat'], el['lon'])
        if el['type'] == 'way':
            for nid, g in zip(el.get('nodes', []), el.get('geometry', [])):
                pos.setdefault(nid, (g['lat'], g['lon']))
            if el.get('tags', {}).get('aeroway') in MOVE:
                ways.append(el)
    use = collections.Counter(n for w in ways for n in set(w['nodes']))
    ends = {n for w in ways for n in (w['nodes'][0], w['nodes'][-1])}
    hold_osm = {el['id']: el for el in osm['elements'] if el['type'] == 'node' and el.get('tags', {}).get('aeroway') == 'holding_position'}
    keep = {n for n, c in use.items() if c >= 2} | ends | (set(hold_osm) & set(use))

    nodes = {}
    def node(nid):
        if nid not in nodes:
            lat, lon = pos[nid]
            nodes[nid] = {'id': f'osm:{nid}', 'lat': round(lat, 7), 'lon': round(lon, 7), 'osm_node': nid, 'refs': set(), 'aeroways': set(), 'hold_short': []}
        return nodes[nid]

    edges = []
    for w in ways:
        t = w.get('tags', {}); ref = t.get('ref') or ''; aw = t['aeroway']
        kind = 'runway' if aw == 'runway' else 'gate_leadin' if ref.isdigit() and int(ref) in gates else aw
        seq = w['nodes']; start = 0
        for i in range(1, len(seq)):
            if seq[i] in keep or i == len(seq) - 1:
                pts = seq[start:i + 1]
                length = sum(dist(pos[p], pos[q]) for p, q in zip(pts, pts[1:]))
                for nid in (seq[start], seq[i]):
                    n = node(nid); n['refs'].add(ref); n['aeroways'].add(aw)
                e = {'id': f'osm:{w["id"]}:{len(edges)}', 'from': f'osm:{seq[start]}', 'to': f'osm:{seq[i]}', 'ref': ref, 'aeroway': aw, 'kind': kind,
                     'length_m': round(length, 1), 'osm_way': w['id'], 'shape': [[round(pos[p][0], 7), round(pos[p][1], 7)] for p in pts]}
                if kind == 'gate_leadin':
                    e.update(gate=int(ref), destination_only=True, note='Gate lead-in (Franco, Oct 8): destination only, never a route element.')
                if aw == 'runway':
                    e['runway_ref'] = ref
                edges.append(e); start = i

    # ---- hold-short nodes ----
    unattached = []
    for nid, el in hold_osm.items():
        if nid in nodes:
            nodes[nid]['hold_short'].append({'source': 'osm_holding_position_node', 'osm_node': nid, 'tags': el.get('tags', {})})
        else:
            unattached.append({'source': 'osm_holding_position_node', 'osm_node': nid, 'reason': 'not on a taxiway/taxilane/runway way'})
    move_nodes = {n for w in ways for n in w['nodes']}
    for el in osm['elements']:
        if el['type'] == 'way' and el.get('tags', {}).get('aeroway') == 'holding_position':
            shared = [n for n in el['nodes'] if n in nodes]
            if shared:
                for n in shared:
                    nodes[n]['hold_short'].append({'source': 'osm_holding_position_way', 'osm_way': el['id'], 'tags': el.get('tags', {})})
                continue
            mid = el['geometry'][len(el['geometry']) // 2]; mp = (mid['lat'], mid['lon'])
            best = min(((dist(mp, (n['lat'], n['lon'])), k) for k, n in nodes.items()), default=None)
            if best and best[0] <= SNAP_HOLD_WAY_M:
                nodes[best[1]]['hold_short'].append({'source': 'osm_holding_position_way', 'osm_way': el['id'], 'snapped_m': round(best[0], 1), 'tags': el.get('tags', {})})
            else:
                unattached.append({'source': 'osm_holding_position_way', 'osm_way': el['id'], 'reason': f'no draft node within {SNAP_HOLD_WAY_M:.0f} m'})
    # chart ILS hold lines (centre of each dashed group), georeferenced with the graticule fit
    rep = json.loads((base / 'review/extract_report.json').read_text())
    A, B, C, D, E, F = gj['transform_page_pt_to_local_m']['coeffs_abcdef']
    chart_holds = []
    for hg in rep.get('hold_line_groups', []):
        x = (hg['bbox'][0] + hg['bbox'][2]) / 2; y = (hg['bbox'][1] + hg['bbox'][3]) / 2
        lat, lon = L.ll(A * x + B * y + C, D * x + E * y + F)
        best = min(((dist((lat, lon), (n['lat'], n['lon'])), k) for k, n in nodes.items()), default=None)
        rec = {'source': 'faa_chart_ils_hold_line', 'chart_group': hg.get('group'), 'annotation': hg.get('nearest_annotation'), 'lat': round(lat, 7), 'lon': round(lon, 7)}
        if best and best[0] <= SNAP_CHART_HOLD_M:
            rec['snapped_m'] = round(best[0], 1); nodes[best[1]]['hold_short'].append(rec); rec['node'] = nodes[best[1]]['id']
        chart_holds.append(rec)

    # ---- NASR runway ends (geometry of record), tied to the nearest OSM runway-way node ----
    rwy_nodes = [k for k, n in nodes.items() if 'runway' in n['aeroways']]
    nasr_ends = []
    with open(base / 'sources/nasr/APT_RWY_END.csv', newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if not r['LAT_DECIMAL'].strip() or r['RWY_END_ID'].startswith('H'):
                continue
            p = (float(r['LAT_DECIMAL']), float(r['LONG_DECIMAL']))
            best = min(((dist(p, (nodes[k]['lat'], nodes[k]['lon'])), k) for k in rwy_nodes), default=None)
            e = {'id': f'nasr:end:{r["RWY_END_ID"]}', 'runway': r['RWY_ID'], 'end': r['RWY_END_ID'], 'lat': p[0], 'lon': p[1], 'source': 'FAA NASR (geometry of record)'}
            if best and best[0] <= SNAP_NASR_END_M:
                e.update(osm_runway_node=nodes[best[1]]['id'], osm_offset_m=round(best[0], 1))
            nasr_ends.append(e)

    # Franco's name rules: OSM is geometry only; chart names win. R4: an unnamed OSM way at a chart label carries the
    # chart name, flagged. Per-way flags from review/human_notes.json (e.g. KORD: OSM tags chart F as S).
    hn_p = base / 'review/human_notes.json'; hn = json.loads(hn_p.read_text()) if hn_p.exists() else {}
    names_p = base / 'review/osm_names_report.json'
    r4 = {x['osm_unnamed_way']: x['label'] for x in json.loads(names_p.read_text()).get('review_list', []) if x.get('rule') == 'R4' and x.get('osm_unnamed_way')} if names_p.exists() else {}
    wflags = {f['osm_way']: f for f in hn.get('osm_name_flags', []) if f.get('osm_way')}
    for e in edges:
        if e['osm_way'] in r4 and not e['ref']:
            e.update(chart_name=r4[e['osm_way']], name_flag='unnamed in OSM; name from the FAA chart (R4)')
        if e['osm_way'] in wflags:
            f = wflags[e['osm_way']]
            e.update(name_flag=f['flag'] + (f" ({f['note']})" if f.get('note') else ''), **({'chart_name': f['label']} if f.get('chart_name_applies', True) else {}))
    used = {e['from'] for e in edges} | {e['to'] for e in edges}
    node_list = [dict(n, refs=sorted(r for r in n['refs'] if r), aeroways=sorted(n['aeroways'])) for n in nodes.values() if n['id'] in used]
    draft = {
        'status': 'DRAFT - NOT WIRED. No runtime code loads this file (tests/run_all.py). Predictions stay OFF on every real airport. Unreviewed.',
        'icao': icao, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'predictions_enabled': False, 'routing_enabled': False,
        'frame': 'WGS84 lat/lon (OSM and NASR coordinates); edge length_m in the local equirectangular frame of georef.json',
        'sources': {'osm': json.loads((base / 'osm/PROVENANCE.json').read_text()).get('osm_data_timestamp') if (base / 'osm/PROVENANCE.json').exists() else None,
                    'osm_licence': 'ODbL 1.0 (derivative database; see ATTRIBUTION.md)', 'nasr': 'sources/nasr (FAA NASR, public domain)',
                    'chart_hold_lines': 'review/extract_report.json hold_line_groups via georef.json (display-grade, ~10 m)'},
        'counts': {'nodes': len(node_list), 'edges': len(edges), 'hold_short_nodes': sum(1 for n in node_list if n['hold_short']),
                   'gate_leadin_edges': sum(1 for e in edges if e['kind'] == 'gate_leadin'), 'nasr_ends': len(nasr_ends),
                   'chart_ils_holds': len(chart_holds), 'chart_ils_holds_snapped': sum(1 for c in chart_holds if 'node' in c), 'unattached_holds': len(unattached)},
        'name_flags': list({e['osm_way']: {'osm_way': e['osm_way'], 'osm_ref': e['ref'] or None, 'chart_name': e.get('chart_name'), 'flag': e['name_flag']} for e in edges if e.get('name_flag')}.values()),
        'missing_geometry': [f for f in hn.get('osm_name_flags', []) if not f.get('osm_way')],
        'nodes': node_list, 'edges': edges, 'nasr_runway_ends': nasr_ends, 'chart_ils_holds': chart_holds, 'unattached_holds': unattached}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'graph_draft.json').write_text(json.dumps(draft, indent=1, ensure_ascii=False))
    (out / 'ATTRIBUTION.md').write_text('# Attribution\n\n`graph_draft.json` is derived from OpenStreetMap data, © OpenStreetMap contributors, available under the '
                                        'Open Database Licence (ODbL) 1.0: https://www.openstreetmap.org/copyright . It also uses FAA NASR runway-end '
                                        'coordinates and FAA-chart hold-line positions (US Government works, public domain).\n\nDRAFT, not wired to any runtime code.\n')
    review(base, draft, out)
    print(json.dumps(draft['counts']))


def review(base, d, out):
    """Diff the draft against the hand-built v1 graph (identifiers and topology only; v1 has no geometry by rule)."""
    hp = REPO / 'data/airports' / f'{base.name}.graph.json'
    if not hp.exists():   # no hand-built v1 graph for this airport: report the draft and Franco's name flags only
        L = [f'# {d["icao"]} graph DRAFT', '', '**DRAFT, NOT WIRED.** No runtime code loads `graph_draft/` (a test enforces this). Predictions stay off. Nothing here is reviewed.', '',
             f"{d['counts']['nodes']} nodes, {d['counts']['edges']} edges, {d['counts']['hold_short_nodes']} hold-short nodes, {d['counts']['gate_leadin_edges']} gate lead-in edges, {d['counts']['nasr_ends']} NASR ends.", '',
             '## Name flags (Franco, Oct 9)']
        L += [f"- OSM way {f['osm_way']} (OSM ref {f['osm_ref'] or 'none'}): " + (f"chart name **{f['chart_name']}**: " if f['chart_name'] else 'review only: ') + f['flag'] for f in d['name_flags']] or ['- none']
        L += ['', '## OSM geometry missing'] + ([f"- **{f['flag']}**" for f in d['missing_geometry']] or ['- none'])
        (out / 'GRAPH_DRAFT_REVIEW.md').write_text('\n'.join(L) + '\n'); return
    hand = json.loads(hp.read_text())
    twy_hand = {s['id'].split(':')[-1] for s in hand['surfaces'] if ':TWY:' in s['id']}
    refs = collections.Counter(e['ref'] for e in d['edges'] if e['ref'] and e['aeroway'] != 'runway' and e['kind'] != 'gate_leadin')
    letters = {r for r in refs if not r.isdigit()}
    byref = collections.defaultdict(set)
    for e in d['edges']:
        for k in ('from', 'to'):
            byref[e['ref']].add(e[k])
    rwy_ref = lambda sid: sid.split(':')[-1].replace('-', '/')   # KLGA:RWY:04-22 -> 04/22
    def surf_nodes(sid):
        if ':RWY:' in sid:
            return set().union(*[byref[r] for r in byref if r.replace('-', '/') in (rwy_ref(sid), rwy_ref(sid).lstrip('0'))]) if byref else set()
        return byref.get(sid.split(':')[-1], set())
    L = []
    L.append(f'# {d["icao"]} graph DRAFT vs hand-built v1 graph (`{base.name}.graph.json`)\n')
    L.append('**DRAFT, NOT WIRED.** No runtime code loads `graph_draft/` (a test enforces this). Predictions stay off. Nothing here is reviewed.')
    L.append('The v1 graph keeps geometry null by rule. This draft has coordinates (OSM + NASR) and is **only** a review aid for a future, separately approved graph.\n')
    c = d['counts']
    L.append('## Draft contents')
    L.append(f'- {c["nodes"]} nodes, {c["edges"]} edges (OSM taxiway / taxilane / runway ways split at junctions); edge lengths from the OSM shape.')
    L.append(f'- {c["hold_short_nodes"]} hold-short nodes (OSM holding_position nodes and ways, and chart ILS hold lines). {c["chart_ils_holds_snapped"]}/{c["chart_ils_holds"]} chart ILS hold lines snapped within {SNAP_CHART_HOLD_M:.0f} m. {c["unattached_holds"]} OSM holds not attached.')
    L.append(f'- {c["gate_leadin_edges"]} gate lead-in edges (destination only, never a route element).')
    L.append(f'- {c["nasr_ends"]} NASR runway ends (geometry of record), each tied to the nearest OSM runway-way node: '
             + ', '.join(f'{e["end"]} {e.get("osm_offset_m", "–")} m' for e in d['nasr_runway_ends']) + '.\n')
    L.append('## Taxiway identifiers')
    L.append(f'- In both: {", ".join(sorted(twy_hand & letters))}')
    L.append(f'- **Draft only (OSM):** {", ".join(sorted(letters - twy_hand)) or "none"}' + ('. **Includes Taxiway H** (KLGA_CHANGES_draft2 item 9: missing from v1).' if 'H' in letters - twy_hand else ''))
    L.append(f'- **Hand only (v1, not in OSM refs):** {", ".join(sorted(twy_hand - letters)) or "none"}\n')
    missing = []
    for n in hand['nodes']:
        sets = [surf_nodes(x) for x in n['surface_ids']]
        if not (sets and all(sets) and set.intersection(*sets)):
            pos = {x['id']: (x['lat'], x['lon']) for x in d['nodes']}
            pairs = [(math.hypot(*(p - q for p, q in zip(G.Local(*pos[a]).en(*pos[b]), (0, 0)))), a, b) for a in sets[0] for b in sets[1] if a in pos and b in pos] if len(sets) == 2 else []
            gap = min(pairs) if pairs else None
            missing.append(f'`{n["id"]}` ({" × ".join(x.split(":")[-1] for x in n["surface_ids"])})'
                           + (f': OSM has no node shared by them; the closest {n["surface_ids"][0].split(":")[-1]} and {n["surface_ids"][1].split(":")[-1]} draft nodes are {gap[0]:.0f} m apart (`{gap[1]}`, `{gap[2]}`)' if gap else ''))
    L.append('## Hand-built junction missing from OSM')
    L += [f'- **{m}**' for m in missing] or ['- none']
    L.append('')
    L.append('## Hand-built junction nodes: does the draft have the junction?')
    for n in hand['nodes']:
        sets = [surf_nodes(s) for s in n['surface_ids']]
        common = set.intersection(*sets) if sets and all(sets) else set()
        L.append(f'- `{n["id"]}` ({n["kind"]}, v1 {n["uncertainty"]["label"]}): ' + (f'**yes**, {len(common)} draft node(s) shared by ' + ' + '.join(n['surface_ids']) + f' (e.g. `{sorted(common)[0]}`)' if common
                 else '**no shared draft node**: ' + ', '.join(f'{s} {len(x)} nodes' for s, x in zip(n['surface_ids'], sets)) + ' (OSM may join them through an unnamed connector or apron)'))
    L.append('\n## Hand-built segments (all along Delta)')
    for s in hand['segments']:
        L.append(f'- `{s["id"]}`: {s["from_node_id"]} → {s["to_node_id"]} on {s["surface_id"]}; v1 {s["uncertainty"]["label"]}, routing_enabled false. The draft has {sum(1 for e in d["edges"] if e["ref"] == s["surface_id"].split(":")[-1])} edges with ref {s["surface_id"].split(":")[-1]}.')
    L.append('\n## Hand-built hold points vs draft hold-short nodes')
    for h in hand['hold_points']:
        ref = h['approach_surface_id'].split(':')[-1]
        cand = [n for n in d['nodes'] if n['hold_short'] and ref in n['refs']]
        srcs = collections.Counter(x['source'] for n in cand for x in n['hold_short'])
        L.append(f'- `{h["id"]}` ({h["kind"]}, marking {h["physical_marking_status"]}): {len(cand)} draft hold-short node(s) on {ref} ' + (f'({", ".join(f"{k} ×{v}" for k, v in srcs.items())})' if cand else '(none)') + '. Which one, if any, is this hold point is a reviewer decision.')
    L.append('\n## Chart ILS hold lines (display-grade georef) → draft')
    for ch in d['chart_ils_holds']:
        L.append(f'- chart group {ch["chart_group"]} (near "{ch["annotation"]}"): ' + (f'snapped to `{ch["node"]}` at {ch["snapped_m"]} m' if 'node' in ch else f'no draft node within {SNAP_CHART_HOLD_M:.0f} m'))
    L.append('\n## Not done (by design)')
    L.append('- Nothing is wired: no routing, no look-ahead, no position inference.')
    L.append('- No turn permissions or direction. Runway protected areas are not modelled. Zones (HS 1, HS 2, Delta crossing) are not drawn.')
    L.append('- The draft is not merged into `klga.graph.json`. Every difference above is a question for a qualified reviewer (`KLGA_CHANGES_draft2.md`).')
    (out / 'GRAPH_DRAFT_REVIEW.md').write_text('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
