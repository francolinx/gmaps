"""Along-track / across-track error of every NASR runway end vs the diagram-derived end, per airport.

  python3 tools/airport_map/runway_ends_review.py --icao KJFK      # -> data/airports/<icao>/review/RUNWAY_ENDS.md (+ .json)
  python3 tools/airport_map/runway_ends_review.py --all

along = error along the runway (NASR end-to-end bearing), across = perpendicular. An end with |along| > 5 m is flagged as
a FIT problem, not drafting: the chart draws runway lengths to ~1 m (diagram vs NASR length differences), so a large
along-track error points at the georeference (graticule values/lines), not at the runway drawing. Stdlib only.
"""
import argparse, json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALONG_FLAG_M = 5.0


def review(icao):
    base = REPO / 'data/airports' / icao.lower(); g = json.loads((base / 'georef.json').read_text())
    grat = json.loads((base / 'graticule.json').read_text())
    rows = [r for r in g.get('runway_ends', []) if 'residual_m' in r]
    flags = [r['id'] for r in rows if abs(r['along_m']) > ALONG_FLAG_M]
    rw = {x['runway']: x for x in g.get('runways', []) if 'length_diff_m' in x}
    by = {r['id']: r for r in rows}
    def diagnose(eid):   # is it the fit (every end shifted) or this one end (runway drawn/extracted short or long)?
        rwy = next((k for k in rw if eid in [e.zfill(2) if e[:1].isdigit() else e for e in k.split('/')] or eid.lstrip('0') in k.split('/')), None)
        if not rwy:
            return 'fit problem (no runway pair to compare)'
        other = next((by[e] for e in by if e != eid and (e in rwy.split('/') or e.lstrip('0') in rwy.split('/'))), None)
        ld = rw[rwy]['length_diff_m']; a = by[eid]['along_m']
        if other and abs(other['along_m']) < 2 and abs(abs(a) - abs(ld)) < 2:
            return (f'this end only: runway {rwy} is drawn {abs(ld):.1f} m {"short" if ld < 0 else "long"} at this end, the other end is within '
                    f'{abs(other["along_m"]):.1f} m and the other runways fit: a chart drawing / extraction issue at this end, not the georeference')
        return 'FIT PROBLEM: the error is not explained by this runway being drawn short or long'
    diag = {e: diagnose(e) for e in flags}
    out = {'icao': icao.upper(), 'graticule_source': grat.get('source') or ('human_transcription' if grat.get('confirmed_by') else None),
           'fit': {k: g['transform_page_pt_to_local_m'].get(k) for k in ('fitted_on', 'graticule_source', 'control_points_n', 'fit_rms_m')},
           'along_flag_m': ALONG_FLAG_M, 'flagged_fit_problems': flags, 'diagnosis': diag,
           'ends': [{k: r[k] for k in ('id', 'residual_m', 'along_m', 'across_m')} for r in rows],
           'runways': [{k: x[k] for k in ('runway', 'nasr_length_ft', 'diagram_length_ft', 'length_diff_m', 'bearing_diff_deg')} for x in rw.values()]}
    (base / 'review/RUNWAY_ENDS.json').write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n')
    L = [f'# {icao.upper()} runway ends: along / across-track error vs NASR', '',
         f'Graticule: {out["graticule_source"]}' + (' (decoded from outlines)' if out['graticule_source'] == 'outline_glyph_decode' else '') +
         f'; fit on {out["fit"]["fitted_on"]}, {out["fit"]["control_points_n"]} control points, RMS {out["fit"]["fit_rms_m"]} m. NASR runway ends are held out.', '',
         f'along = error along the runway, across = perpendicular (m; diagram minus NASR). **|along| > {ALONG_FLAG_M:.0f} m is flagged as a fit problem, not drafting.**', '',
         '| end | total m | along m | across m | flag |', '|---|---|---|---|---|']
    L += [f"| {r['id']} | {r['residual_m']:.2f} | {r['along_m']:+.2f} | {r['across_m']:+.2f} | {('**flagged (along > 5 m)**: ' + diag[r['id']]) if r['id'] in flags else ''} |" for r in rows]
    L += ['', '| runway | NASR length ft | diagram length ft | length diff m | bearing diff ° |', '|---|---|---|---|---|']
    L += [f"| {x['runway']} | {x['nasr_length_ft']} | {x['diagram_length_ft']} | {x['length_diff_m']:+.1f} | {x['bearing_diff_deg']:+.3f} |" for x in out['runways']]
    L += ['', ('Flagged (|along| > 5 m, Franco Oct 9): ' + '; '.join(f'{e}: {diag[e]}' for e in flags)) if flags else 'No end has along-track error above 5 m: across-track error dominates (the runway is drawn slightly rotated or offset sideways, i.e. drafting).']
    (base / 'review/RUNWAY_ENDS.md').write_text('\n'.join(L) + '\n')
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--icao'); ap.add_argument('--all', action='store_true'); a = ap.parse_args()
    todo = [p.name.upper() for p in sorted((REPO / 'data/airports').iterdir()) if (p / 'georef.json').exists()] if a.all else [a.icao.upper()]
    for icao in todo:
        o = review(icao); worst = max(o['ends'], key=lambda r: abs(r['along_m']))
        print(f"{icao}: max |along| {abs(worst['along_m']):.2f} m ({worst['id']}), max |across| {max(abs(r['across_m']) for r in o['ends']):.2f} m, flagged {o['flagged_fit_problems']}")
