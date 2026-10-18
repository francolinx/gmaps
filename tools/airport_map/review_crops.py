"""Review crops: the FAA chart (rendered north-up) around a point, with optional marks and OSM ways drawn on top.

  .venv/bin/python tools/airport_map/review_crops.py labels   --icao KLGA --text A --text B --text C   # unmatched name instances
  .venv/bin/python tools/airport_map/review_crops.py labels   --icao KLGA --class terminal_label       # every label of a class
  .venv/bin/python tools/airport_map/review_crops.py numbered --icao KLGA --ref 5 --ref 9 --ref 16 --ref 14

Writes PNGs to data/airports/<icao>/review/crops/. Red box = the chart label in question; magenta line = the OSM way(s)
with the same ref (© OpenStreetMap contributors, ODbL; stamped on every crop that shows OSM). Review only.
Needs pdftoppm and Pillow + numpy (.venv).
"""
import argparse, collections, json, subprocess, sys, tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import georef as G   # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DPI = 300
K = DPI / 72.0
OSM_NOTE = '© OpenStreetMap contributors, ODbL'


def chart(base, meta):
    cache = base / 'build' / f'chart_northup_{DPI}.png'
    if not cache.exists():
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(['pdftoppm', '-r', str(DPI), '-png', '-singlefile', str(REPO / meta['pdf']), f'{td}/c'], check=True)
            a = np.asarray(Image.open(f'{td}/c.png').convert('RGB'))
            if meta['content_rotation_deg'] == 90:
                a = np.transpose(np.flipud(a), (1, 0, 2))
            Image.fromarray(a).save(cache)
    return Image.open(cache).convert('RGB')


def inverse(T):
    a, b, c, d, e, f = T
    det = a * e - b * d
    return lambda E, N: ((e * (E - c) - b * (N - f)) / det, (-d * (E - c) + a * (N - f)) / det)


def crop(img, centre, half, boxes=(), lines=(), title='', note=''):
    x0, y0 = int((centre[0] - half) * K), int((centre[1] - half) * K)
    c = img.crop((x0, y0, x0 + int(2 * half * K), y0 + int(2 * half * K))).copy()
    dr = ImageDraw.Draw(c)
    for b in boxes:
        dr.rectangle([(b[0] * K - x0 - 3, b[1] * K - y0 - 3), (b[2] * K - x0 + 3, b[3] * K - y0 + 3)], outline=(220, 0, 0), width=3)
    for pts, col in lines:
        dr.line([(x * K - x0, y * K - y0) for x, y in pts], fill=col, width=4)
    probe = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    def wrap(text):   # word-wrap to the crop width so nothing is cut off
        lines, cur = [], ''
        for w in text.split(' '):
            t = (cur + ' ' + w).strip()
            if cur and probe.textlength(t) > c.width - 12:
                lines.append(cur); cur = w
            else:
                cur = t
        return lines + [cur]
    rows = [(l, (0, 0, 0)) for l in wrap(title)] + [(l, (90, 90, 90)) for l in wrap(note)]
    bh = 8 + 14 * len(rows)
    band = Image.new('RGB', (c.width, bh), (255, 255, 255)); db = ImageDraw.Draw(band)
    for i, (l, col) in enumerate(rows):
        db.text((6, 4 + 14 * i), l, fill=col)
    out = Image.new('RGB', (c.width, c.height + bh), (255, 255, 255)); out.paste(band, (0, 0)); out.paste(c, (0, bh))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('labels', 'numbered')); ap.add_argument('--icao', required=True)
    ap.add_argument('--text', action='append', default=[]); ap.add_argument('--class', dest='label_class'); ap.add_argument('--ref', action='append', default=[])
    ap.add_argument('--half', type=float, default=45.0, help='half-width of the crop in chart points')
    a = ap.parse_args()
    base = REPO / 'data/airports' / a.icao.lower()
    raw = json.loads((base / 'build/layers.raw.json').read_text())
    img = chart(base, raw['meta'])
    outdir = base / 'review/crops'; outdir.mkdir(parents=True, exist_ok=True)
    gj = json.loads((base / 'georef.json').read_text())
    L = G.Local(gj['local_frame']['lat0'], gj['local_frame']['lon0']); inv = inverse(gj['transform_page_pt_to_local_m']['coeffs_abcdef'])
    to_page = lambda lat, lon: inv(*L.en(lat, lon))
    centre = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    written = []
    if a.cmd == 'labels' and a.label_class:
        for k, lab in enumerate(l for l in raw['labels'] if l['class'] == a.label_class):
            name = f'{a.label_class}_{lab["text"]}_{k}.png'
            crop(img, centre(lab['bbox']), a.half, boxes=[lab['bbox']], title=f'Chart label "{lab["text"]}" classed {a.label_class}',
                 note='FAA airport diagram, cycle 2610 (public domain). Red box = the label.').save(outdir / name)
            written.append(name)
    elif a.cmd == 'labels':
        rep = json.loads((base / 'review/osm_names_report.json').read_text())
        for t in a.text:
            for k, r in enumerate(x for x in rep['instances'] if x['label'] == t and x['status'] != 'match'):
                lab = min((l for l in raw['labels'] if l['text'] == t), key=lambda l: abs(centre(l['bbox'])[0] - r['chart_page_pt'][0]) + abs(centre(l['bbox'])[1] - r['chart_page_pt'][1]))
                name = f'label_{t}_{k}.png'
                crop(img, r['chart_page_pt'], a.half, boxes=[lab['bbox']], title=f'Chart label "{t}" at page {r["chart_page_pt"]} ({r["status"]})',
                     note='FAA airport diagram, cycle 2610 (public domain). Red box = the label in question.').save(outdir / name)
                written.append(name)
    else:
        osm = json.loads((base / 'osm/overpass_raw.json').read_text())
        for ref in a.ref:
            chart_l = [l for l in raw['labels'] if l['text'] == ref and l['class'] == 'gate_leadin']
            ways = [e for e in osm['elements'] if e['type'] == 'way' and e.get('tags', {}).get('ref') == ref and e['tags'].get('aeroway') in ('taxiway', 'taxilane')]
            lines = [([to_page(p['lat'], p['lon']) for p in w['geometry']], (220, 0, 200)) for w in ways]
            if chart_l:
                c = centre(chart_l[0]['bbox'])
            elif lines:
                pts = [p for ln, _ in lines for p in ln]; c = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            else:
                continue
            if not ways:   # chart-only number: show the nearest OSM numbered ways for context
                num = [e for e in osm['elements'] if e['type'] == 'way' and str(e.get('tags', {}).get('ref', '')).isdigit()]
                d2 = lambda e: min((to_page(p['lat'], p['lon'])[0] - c[0]) ** 2 + (to_page(p['lat'], p['lon'])[1] - c[1]) ** 2 for p in e['geometry'])
                near = sorted(num, key=d2)[:4]
                lines = [([to_page(p['lat'], p['lon']) for p in w['geometry']], (0, 150, 220)) for w in near]
                note = (f'No OSM taxiway/taxilane with ref {ref}. Blue = nearest OSM ways with a numeric ref: '
                        + ', '.join(f"{w['tags']['ref']} ({w['tags'].get('aeroway')})" for w in sorted(near, key=lambda w: int(w['tags']['ref']))) + f'. {OSM_NOTE}.')
            else:
                kinds = collections.Counter(w['tags']['aeroway'] for w in ways)
                note = f'Magenta = {len(ways)} OSM way(s) with ref {ref} (' + ', '.join(f'{n} {k}' for k, n in kinds.items()) + f'). {OSM_NOTE}.'
            name = f'numbered_{ref}.png'
            crop(img, c, a.half, boxes=[l['bbox'] for l in chart_l], lines=lines,
                 title=f'Chart number "{ref}" (class gate_leadin) vs OSM ref {ref}', note=note).save(outdir / name)
            written.append(name)
    print('\n'.join(str(outdir / w) for w in written))


if __name__ == '__main__':
    main()
