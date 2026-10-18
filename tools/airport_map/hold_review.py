"""Hold-line review crops: every hold annotation (ILS / CAT / AREA HOLD, ILS CRITICAL AREA) and every detected hold bar,
cut from the FAA PDF (north-up, 500 dpi) with the bar boxed in red and leader tips marked. Review only.

  .venv/bin/python tools/airport_map/hold_review.py --icao KATL
  -> data/airports/<icao>/review/hold_lines/{bar_<n>.png, annotation_<n>_<found|MISSED>.png, HOLD_LINES.md}
"""
import argparse, json, math, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
DPI, R = 500, 30


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); a = ap.parse_args()
    base = REPO / 'data/airports' / a.icao.lower(); out = base / 'review/hold_lines'
    raw = json.loads((base / 'build/layers.raw.json').read_text()); meta = raw['meta']; rot = meta['content_rotation_deg']
    rep = json.loads((base / 'review/extract_report.json').read_text()); holds = rep['hold_line_groups']
    cen = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    ann = []
    for l in raw['labels']:
        if l.get('hold_annotation'):
            c = cen(l['bbox']); same = next((x for x in ann if math.dist(c, x['c']) <= 14), None)
            if same:
                same['tips'] += l.get('hold_leader_tips', []); same['text'] += ' / ' + l['text']
            else:
                ann.append({'c': c, 'text': l['text'], 'tips': list(l.get('hold_leader_tips', []))})
    if not ann and not holds:
        print('no hold annotations or bars'); return
    s = DPI / 72
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(['pdftoppm', '-r', str(DPI), '-png', '-singlefile', str(REPO / meta['pdf']), str(Path(td) / 'f')], check=True)
        im = np.array(Image.open(Path(td) / 'f.png').convert('RGB'))
    if rot == 90: im = np.transpose(np.flipud(im), (1, 0, 2))
    elif rot == 270: im = np.transpose(np.fliplr(im), (1, 0, 2))
    elif rot == 180: im = im[::-1, ::-1]
    img = Image.fromarray(np.ascontiguousarray(im)); out.mkdir(parents=True, exist_ok=True)

    def crop(cx, cy, boxes=(), tips=(), title=''):
        t = img.crop((int((cx - R) * s), int((cy - R) * s), int((cx + R) * s), int((cy + R) * s))).copy(); d = ImageDraw.Draw(t)
        px = lambda x, y: ((x - cx + R) * s, (y - cy + R) * s)
        for b in boxes:
            (x0, y0), (x1, y1) = px(b[0], b[1]), px(b[2], b[3]); d.rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), outline=(230, 0, 0), width=4)
        for tp in tips:
            x, y = px(*tp); d.ellipse((x - 7, y - 7, x + 7, y + 7), outline=(0, 90, 230), width=3)
        t = t.resize((360, 360)); ImageDraw.Draw(t).rectangle((0, 0, 359, 14), fill=(255, 255, 255)); ImageDraw.Draw(t).text((3, 2), title[:60], fill=(0, 0, 0))
        return t

    near = lambda p, g, dd: math.dist(p, cen(g['bbox'])) <= dd
    L = [f'# {a.icao.upper()} hold lines: annotations and detected bars', '',
         'Crops from the FAA PDF (north-up, 500 dpi): red box = detected bar, blue circle = leader tip. `tools/airport_map/hold_review.py`.', '',
         '| annotation | at (pt) | leader tips | bar found | crop |', '|---|---|---|---|---|']
    for i, x in enumerate(ann):
        cov = [g for g in holds if near(x['c'], g, 50) or any(near(tp, g, 20) for tp in x['tips'])]
        fn = f'annotation_{i}_{"found" if cov else "MISSED"}.png'
        cx, cy = x['c']
        crop(cx, cy, [g['bbox'] for g in cov if near((cx, cy), g, R)], [tp for tp in x['tips'] if math.dist(tp, (cx, cy)) < R], f'{x["text"]} {"found" if cov else "MISSED"}').save(out / fn)
        L.append(f'| {x["text"]} | {cx:.0f}, {cy:.0f} | {len(x["tips"])} | {"yes (" + ", ".join("bar " + str(g["group"]) for g in cov) + ")" if cov else "**no**"} | `{fn}` |')
    L += ['', '| bar | dashes | rule | crop |', '|---|---|---|---|']
    for g in holds:
        fn = f'bar_{g["group"]}.png'; c = cen(g['bbox'])
        crop(*c, [g['bbox']], [], f'bar {g["group"]}: {g["nearest_annotation"]}').save(out / fn)
        rule = 'leader tip (single or bent dashes)' if 'at leader tip' in g['nearest_annotation'] else 'collinear dashes, near leader tip' if 'via leader' in g['nearest_annotation'] else 'collinear dashes, near annotation'
        L.append(f'| {g["group"]} | {g["dashes"]} | {rule} | `{fn}` |')
    (out / 'HOLD_LINES.md').write_text('\n'.join(L) + '\n'); print(f'{len(ann)} annotations, {len(holds)} bars -> {out.relative_to(REPO)}')


if __name__ == '__main__':
    main()
