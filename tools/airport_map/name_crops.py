"""Name-review crops: for every taxiway-name row Franco asked to see, the FAA chart (left) and the OSM
taxiway layer with its refs (right, ODbL) at the same ground extent, side by side. Review only; nothing is decided here.

  .venv/bin/python tools/airport_map/name_crops.py        # rows from data/airports/*/review/human_notes.json -> docs/name_crops/
"""
import json, math, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'tools/airport_map'))
import georef as G   # noqa: E402

OUT = REPO / 'docs/name_crops'
DPI, HALF_PT, PX = 400, 25.0, 520   # 25 chart points either side of the label (same paper extent on every chart)


def faa_image(meta):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(['pdftoppm', '-r', str(DPI), '-png', '-singlefile', str(REPO / meta['pdf']), str(Path(td) / 'f')], check=True)
        im = np.array(Image.open(Path(td) / 'f.png').convert('RGB'))
    rot = meta['content_rotation_deg']
    if rot == 90: im = np.transpose(np.flipud(im), (1, 0, 2))
    elif rot == 270: im = np.transpose(np.fliplr(im), (1, 0, 2))
    elif rot == 180: im = im[::-1, ::-1]
    return Image.fromarray(np.ascontiguousarray(im))


def main():
    OUT.mkdir(parents=True, exist_ok=True); made = []
    for hn_p in sorted((REPO / 'data/airports').glob('*/review/human_notes.json')):
        hn = json.loads(hn_p.read_text())
        rows = [dict(r, question=r['question']) for r in (hn.get('taxiway_names_pending_crop') or {}).get('rows', [])] + \
               [dict(r, question=f"DECIDED ({r['rule']}): {r['decision']}") for r in (hn.get('taxiway_names_decisions') or {}).get('rows', [])] + \
               [dict(r, question=f"OPEN: {r['note']}") for r in hn.get('open_items', []) if 'lat' in r]
        if not rows:
            continue
        base = hn_p.parents[1]; icao = base.name
        gj = json.loads((base / 'georef.json').read_text()); L = G.Local(gj['local_frame']['lat0'], gj['local_frame']['lon0'])
        a, b, c, d, e, f = gj['transform_page_pt_to_local_m']['coeffs_abcdef']; det = a * e - b * d
        to_pt = lambda E, N: ((e * (E - c) - b * (N - f)) / det, (-d * (E - c) + a * (N - f)) / det)
        meta = json.loads((base / 'build/layers.raw.json').read_text())['meta']; img = faa_image(meta); s = DPI / 72
        osm = [ft for ft in json.loads((base / 'osm/osm_aeroways.geojson').read_text())['features']
               if ft['properties'].get('aeroway') in ('taxiway', 'taxilane', 'runway') and ft['geometry']['type'] == 'LineString']
        HALF_M = HALF_PT * math.hypot(a, d)   # metres per chart point
        for i, r in enumerate(rows, 1):
            E0, N0 = L.en(r['lat'], r['lon'])
            corners = [to_pt(E0 + dx, N0 + dy) for dx, dy in ((-HALF_M, HALF_M), (HALF_M, -HALF_M))]
            x0, y0 = min(p[0] for p in corners) * s, min(p[1] for p in corners) * s; x1, y1 = max(p[0] for p in corners) * s, max(p[1] for p in corners) * s
            left = img.crop((int(x0), int(y0), int(x1), int(y1))).resize((PX, PX))
            dl = ImageDraw.Draw(left); dl.ellipse((PX / 2 - 9, PX / 2 - 9, PX / 2 + 9, PX / 2 + 9), outline=(0, 120, 255), width=3)
            right = Image.new('RGB', (PX, PX), 'white'); dr = ImageDraw.Draw(right)
            px = lambda E, N: ((E - E0 + HALF_M) / (2 * HALF_M) * PX, (N0 - N + HALF_M) / (2 * HALF_M) * PX)
            for ft in osm:
                pts = [px(*L.en(q[1], q[0])) for q in ft['geometry']['coordinates']]
                if not any(-50 <= x <= PX + 50 and -50 <= y <= PX + 50 for x, y in pts):
                    continue
                rw = ft['properties']['aeroway'] == 'runway'
                dr.line(pts, fill=(60, 60, 60) if rw else (200, 40, 140), width=8 if rw else 3)
                ref = ft['properties'].get('ref') or '(no ref)'
                mid = pts[len(pts) // 2]
                if 0 <= mid[0] <= PX and 0 <= mid[1] <= PX:
                    dr.rectangle((mid[0] - 2, mid[1] - 2, mid[0] + 7 * len(ref) + 4, mid[1] + 13), fill=(255, 255, 255)); dr.text((mid[0] + 2, mid[1]), ref, fill=(150, 0, 90))
            dr.ellipse((PX / 2 - 9, PX / 2 - 9, PX / 2 + 9, PX / 2 + 9), outline=(0, 120, 255), width=3)
            sheet = Image.new('RGB', (2 * PX + 10, PX + 36), 'white'); sheet.paste(left, (0, 36)); sheet.paste(right, (PX + 10, 36))
            ds = ImageDraw.Draw(sheet)
            ds.text((6, 4), f"{icao.upper()} chart label {r['label']} at {r['lat']:.6f}, {r['lon']:.6f} (blue circle): {r['question']}", fill=(0, 0, 0))
            ds.text((6, 20), f"left: FAA chart (cycle {json.loads((base / 'provenance.json').read_text())['chart']['cycle']})    right: OSM taxiways with refs, (c) OpenStreetMap contributors, ODbL    {2 * HALF_M:.0f} m square", fill=(80, 80, 80))
            tag = 'decided' if r['question'].startswith('DECIDED') else 'open' if r['question'].startswith('OPEN') else 'pending'
            fn = OUT / f'{icao}_{r["label"]}_{tag}_{i}.png'; sheet.save(fn, optimize=True); made.append(fn.name)
    (OUT / 'README.md').write_text('# Taxiway-name crops for Franco (Oct 9)\n\nLeft: FAA chart. Right: OSM taxiway layer with refs (© OpenStreetMap contributors, ODbL), same ground square (50 chart points across), blue circle = the chart label. '
                                   'Made by `tools/airport_map/name_crops.py` from `data/airports/*/review/human_notes.json`. Nothing here is decided.\n\n' + '\n'.join(f'- `{m}`' for m in made) + '\n')
    print(len(made), 'crops ->', OUT.relative_to(REPO))


if __name__ == '__main__':
    main()
