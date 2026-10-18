"""Assemble data/airports/<icao>/provenance.json: where every input came from, and the cycle line the map view shows.

  python3 tools/airport_map/provenance.py --icao KLGA

Chart cycle facts come from data/airports/<icao>/source_chart.json (tools/airport_map/dtpp.py) or, for KLGA, the source record
in data/airports/<icao>.graph.json, and are CHECKED against the PDF
itself: sha256 recomputed, and the effective dates parsed from the chart's own cycle banner ("NE-2, 01 OCT 2026 to
29 OCT 2026", extracted as visible text). A mismatch stops the build. The banner is not drawn on the map; its
content is carried here and shown by the map view's footer (display.footer). A6 (tools/build_airport.py) checks
staleness against effective_to. Stdlib only.
"""
import argparse, datetime, hashlib, json, re, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MONTHS = {m: i for i, m in enumerate(('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'), 1)}


def banner_dates(text):
    m = re.search(r'(\d{1,2}) ([A-Z]{3}) (\d{4}) to (\d{1,2}) ([A-Z]{3}) (\d{4})', text)
    if not m:
        return None
    d = lambda dd, mm, yy: datetime.date(int(yy), MONTHS[mm], int(dd)).isoformat()
    return d(*m.group(1, 2, 3)), d(*m.group(4, 5, 6))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True)
    a = ap.parse_args()
    icao = a.icao.upper(); base = REPO / 'data/airports' / icao.lower()
    raw = json.loads((base / 'build/layers.raw.json').read_text())
    if (base / 'source_chart.json').exists():   # written by tools/airport_map/dtpp.py (generic path)
        src = json.loads((base / 'source_chart.json').read_text())
        if not src.get('chart_code'):   # the chart's own code, e.g. "AL-5015 (FAA)", read from its text layer
            code = next((l['text'] for l in raw['labels'] if re.fullmatch(r'AL-\d+ \(FAA\)', l['text'])), None)
            # no text layer (outline-only chart): the FAA airport-diagram number is the d-TPP 'alnum' (AL-469 = 00469AD.PDF)
            src['chart_code'] = code or (f"AL-{src['airport']['alnum']} (FAA), number from the d-TPP record (chart has no text layer)" if src.get('airport', {}).get('alnum') else 'unknown')
    else:                                        # hand-assembled source record (KLGA, before dtpp.py existed)
        graph = json.loads((REPO / 'data/airports' / f'{icao.lower()}.graph.json').read_text())
        src = next(s for s in graph['sources'] if s['kind'] == 'faa_diagram')
    pdf = REPO / 'data/airports' / src['local_path']
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    banners = sorted({l['text'] for l in raw['labels'] if l.get('visible_text') and banner_dates(l['text'])})
    parsed = {banner_dates(b) for b in banners}
    problems = []
    if sha != src['sha256'] or sha != raw['meta']['pdf_sha256']:
        problems.append(f'PDF sha256 {sha} differs from the source record or the extraction')
    if parsed != {(src['effective_from'], src['effective_to'])}:
        problems.append(f'chart banner dates {sorted(parsed)} != source record {src["effective_from"]}..{src["effective_to"]}')
    cycle = re.search(r'/d-tpp/(\d{4})/', src['uri']).group(1)
    if problems:
        raise SystemExit('; '.join(problems))
    nasr = json.loads((base / 'sources/nasr/PROVENANCE.json').read_text()) if (base / 'sources/nasr/PROVENANCE.json').exists() else None
    grat = json.loads((base / 'graticule.json').read_text()) if (base / 'graticule.json').exists() else None
    rv = json.loads((base / 'review.json').read_text()) if (base / 'review.json').exists() else {}   # a failed runway-end check is said in the footer, never hidden
    rwe_fail = ': outside the 30 m display limit' if next((i.get('status') for i in rv.get('items', []) if i['id'] == 'runway_ends'), None) == 'fail' else ''
    prev = json.loads((base / 'provenance.json').read_text()) if (base / 'provenance.json').exists() else {}
    out = {
        'icao': icao, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'chart': {'publisher': 'FAA Aeronautical Information Services, d-TPP airport diagram', 'title': src['title'], 'chart_code': src['chart_code'],
                  'cycle': cycle, 'effective_from': src['effective_from'], 'effective_to': src['effective_to'],
                  'printed_cycle_banner': banners[0] if banners else None, 'banner_checked_against_pdf': True,
                  'url': src['uri'], 'local_path': str(pdf.relative_to(REPO)), 'sha256': sha, 'retrieved_on': src['retrieved_on'],
                  'licence': 'US Government work (public domain)', 'note': 'Not for navigation.'},
        'nasr': nasr and {k: nasr[k] for k in ('source', 'cycle_effective', 'url', 'zip_sha256', 'retrieved_at', 'licence')},
        'graticule': grat and {'values': [p['value'] for p in grat['parallels']] + [m['value'] for m in grat['meridians']],
                               'source': grat.get('source') or 'human_transcription', 'confirmed_by': grat.get('confirmed_by'),
                               'confirmed_at': grat.get('confirmed_at'), 'read_at': grat.get('read_at')},
        'osm': prev.get('osm'),   # written by tools/airport_map/osm.py (A3); kept here, never merged into chart-derived layers
        'tools': {'extract': raw['meta']['extractor'], 'styles': raw['meta']['styles']},
        'display': {
            'footer': f"Reconstructed from FAA airport diagram {src['chart_code'].split(';')[0]}, d-TPP cycle {cycle} "
                      f"(effective {src['effective_from']} to {src['effective_to']}), sha256 {sha[:12]}…, {src['uri']}. "
                      + (f"Graticule: decoded from outlines (checked against NASR runway ends{rwe_fail}). " if grat and grat.get('source') == 'outline_glyph_decode' else "")
                      + "Display only. Not for navigation. No predictions.",
            'must_show': ['chart.cycle', 'chart.effective_from', 'chart.effective_to', 'chart.sha256', 'chart.url'],
        },
    }
    (base / 'provenance.json').write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps(out['chart'], indent=1)); print('footer:', out['display']['footer'])


if __name__ == '__main__':
    main()
