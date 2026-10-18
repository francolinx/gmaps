"""Create an airport's review checklist (data/airports/<icao>/review.json) from the one generic template. Stdlib only.

  python3 tools/airport_map/checklist.py --icao KHWD

Never overwrites an existing review.json (KLGA's is signed). Items and thresholds are the same for every airport;
the tools score them (status not_run -> pass / fail / review / not_applicable). Sign-off is Franco's only.
"""
import argparse, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ITEMS = [
    {'id': 'overlay_ink', 'check': 'Reconstruction (original FAA styling) rendered at the same scale as the FAA PDF: FAA ink covered and extra ink, 1 px tolerance at 300 dpi.',
     'by': 'tools/airport_map/overlay_check.py', 'thresholds': {'min_covered_pct': 99.0, 'max_extra_pct': 2.0}, 'thresholds_note': 'same for every airport (fixed before the first KLGA measurement)'},
    {'id': 'runway_ends', 'check': 'Every NASR runway end within 30 m of the diagram-derived end (display sign-off threshold, Franco D3). Predictions will need a stricter threshold (~10 m), to be set later.',
     'by': 'tools/airport_map/georef.py', 'thresholds': {'max_residual_m': 30.0}},
    {'id': 'runway_identifiers', 'check': 'Runway count and end identifiers match NASR and the diagram labels exactly.', 'by': 'tools/airport_map/georef.py'},
    {'id': 'taxiway_names', 'check': 'Every diagram taxiway label is matched to an OSM ref, or listed and explained.', 'by': 'tools/airport_map/osm.py'},
    {'id': 'hot_spots', 'check': 'Hot-spot label callouts match the hot-spot outlines on the diagram (not_applicable if the chart has none).', 'by': 'tools/airport_map/extract.py'},
    {'id': 'ils_hold_lines', 'check': 'Each ILS HOLD annotation has its dashed hold line found (not_applicable if the chart has none; FAA APDs do not draw every hold position).', 'by': 'tools/airport_map/extract.py'},
    {'id': 'lahso_points', 'check': 'Each LAHSO callout has its dashed hold-short marker across a runway (n labels / n markers; not_applicable if the chart has none). Display only.', 'by': 'tools/airport_map/extract.py'},
    {'id': 'unclassified', 'check': 'No unclassified objects, or each listed one reviewed.', 'by': 'tools/airport_map/extract.py'},
    {'id': 'cycle_current', 'check': "Today is inside the diagram's effective window, FAA's d-TPP metafile lists the same APD, and the served PDF has the same sha256.", 'by': 'tools/airport_map/cycle_check.py'},
]


GEOMETRY_OF_RECORD = {
    'decision': 'Chart-derived (reconstructed) geometry is for DISPLAY only. Any future prediction logic must use NASR runway-end coordinates (and the reviewed OSM network) as the geometry of record, never chart-traced positions. Predictions stay OFF until this checklist passes and Franco signs off, and are then a separate change.',
    'decided_by': 'Franco', 'decided_at': '2026-10-08',
    'scope_note': 'Decided for KLGA on 2026-10-08; applied to every airport by the checklist template (tools/airport_map/checklist.py).'}


def create(icao):
    base = REPO / 'data/airports' / icao.lower(); p = base / 'review.json'
    if p.exists():
        rv = json.loads(p.read_text())
        if not rv.get('sign_off'):   # unsigned airports get items added to the template later (never a signed one)
            have = {i['id'] for i in rv['items']}
            added = [dict(i, status='not_run') for i in ITEMS if i['id'] not in have]
            if added:
                rv['items'] += added; p.write_text(json.dumps(rv, indent=1, ensure_ascii=False)); print(f'{p.relative_to(REPO)}: added {[i["id"] for i in added]}')
        if 'geometry_of_record' not in rv:   # add the general rule; never touch items or a sign-off
            rv['geometry_of_record'] = GEOMETRY_OF_RECORD; p.write_text(json.dumps(rv, indent=1, ensure_ascii=False)); print(f'{p.relative_to(REPO)}: added geometry_of_record')
        else:
            print(f'{p.relative_to(REPO)} exists; not overwritten')
        return
    src = json.loads((base / 'source_chart.json').read_text())
    rv = {'icao': icao, 'about': 'Review checklist for reconstructed DISPLAY geometry (CLAUDE.md: reconstructed display geometry). Script-checked items are updated by tools/airport_map/*; sign-off is Franco\'s only. Predictions, routing or position inference on this airport stay OFF until every item passes AND it is signed off, and even then they are a separate change.',
          'faa_cycle': src['dtpp_cycle'], 'effective_from': src['effective_from'], 'effective_to': src['effective_to'],
          'items': [dict(i, status='not_run') for i in ITEMS], 'geometry_of_record': GEOMETRY_OF_RECORD, 'signed_off_by': None, 'signed_off_at': None}
    p.write_text(json.dumps(rv, indent=1, ensure_ascii=False)); print(f'created {p.relative_to(REPO)}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); create(ap.parse_args().icao.upper())
