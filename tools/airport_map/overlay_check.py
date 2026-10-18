"""Overlay check: render the reconstruction and the FAA PDF at the same scale and compare ink. Display QA only.

  .venv/bin/python tools/airport_map/overlay_check.py --icao KLGA [--dpi 300]

- FAA side: `pdftoppm` renders the PDF; the raster is rotated into the same north-up frame as the extraction.
- Reconstruction side: headless Chrome renders review/faithful.svg, which uses the ORIGINAL FAA colours, paint order,
  fill rules, dashes and clips (no review/preview styling). Visible-text labels (e.g. the cycle-date banner) are drawn
  with a substitute font, so they match only approximately.
- Ink = any pixel darker than INK_LEVEL. A pixel counts as matched if the other image has ink within TOL_PX pixels.
  covered = FAA ink matched by reconstruction ink / FAA ink;  extra = reconstruction ink with no FAA ink nearby / reconstruction ink.
- Pass/fail thresholds live in data/airports/<icao>/review.json (item overlay_ink); results go to review/overlay_check.json,
  and a diff image to review/overlay_diff.png (red = FAA ink missing from the reconstruction, blue = extra ink).
Needs pdftoppm (poppler), google-chrome, and Pillow + numpy in .venv.
"""
import argparse, json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
INK_LEVEL = 250   # 0-255 gray; anything darker than near-white is ink (pavement gray 0.812 = 207 counts)
TOL_PX = 1


def dilate(m, r):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def gray(path):
    return np.asarray(Image.open(path).convert('L'), dtype=np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--icao', required=True); ap.add_argument('--dpi', type=int, default=300)
    a = ap.parse_args()
    base = REPO / 'data/airports' / a.icao.lower()
    meta = json.loads((base / 'build/layers.raw.json').read_text())['meta']
    review_path = base / 'review.json'
    review = json.loads(review_path.read_text())
    item = next(i for i in review['items'] if i['id'] == 'overlay_ink')
    W, H = meta['page_size_pt']; FW, FH = meta['frame_size_pt']; rot = meta['content_rotation_deg']
    s = a.dpi / 72.0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        subprocess.run(['pdftoppm', '-r', str(a.dpi), '-gray', '-png', '-singlefile', str(REPO / meta['pdf']), str(td / 'faa')], check=True)
        faa = gray(td / 'faa.png')
        if rot == 90:            # extraction frame: X = y_up, Y = x  ->  N[Y, X] = P[H-1-X, Y]
            faa = np.flipud(faa).T
        elif rot == 270:
            faa = np.fliplr(faa).T
        elif rot == 180:
            faa = faa[::-1, ::-1]
        ph, pw = faa.shape
        svg = (base / 'review/faithful.svg').read_text().replace(f'width="{FW}" height="{FH}"', f'width="{pw}" height="{ph}"', 1)
        (td / 'r.svg').write_text(svg)
        chrome = shutil.which('google-chrome') or shutil.which('chromium')
        subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
                        f'--window-size={pw},{ph}', f'--screenshot={td / "rec.png"}', (td / 'r.svg').as_uri()],
                       check=True, capture_output=True, timeout=120)
        rec = gray(td / 'rec.png')[:ph, :pw]
    if rec.shape != faa.shape:
        raise SystemExit(f'size mismatch faa={faa.shape} rec={rec.shape}')
    fi, ri = faa < INK_LEVEL, rec < INK_LEVEL
    covered = float((fi & dilate(ri, TOL_PX)).sum() / max(1, fi.sum()))
    extra = float((ri & ~dilate(fi, TOL_PX)).sum() / max(1, ri.sum()))
    diff = np.full(faa.shape + (3,), 255, np.uint8)
    diff[fi & ri] = (170, 170, 170)
    diff[fi & ~dilate(ri, TOL_PX)] = (220, 0, 0)
    diff[ri & ~dilate(fi, TOL_PX)] = (0, 60, 220)
    Image.fromarray(diff).save(base / 'review/overlay_diff.png', optimize=True)
    th = item['thresholds']
    ok = covered * 100 >= th['min_covered_pct'] and extra * 100 <= th['max_extra_pct']
    res = {'icao': a.icao.upper(), 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'dpi': a.dpi, 'ink_level': INK_LEVEL, 'tolerance_px': TOL_PX,
           'pdf_sha256': meta['pdf_sha256'], 'faa_ink_px': int(fi.sum()), 'reconstruction_ink_px': int(ri.sum()),
           'covered_pct': round(covered * 100, 3), 'extra_pct': round(extra * 100, 3), 'thresholds': th, 'pass': ok,
           'note': 'faithful.svg uses original FAA styling; visible-text labels use a substitute font.'}
    (base / 'review/overlay_check.json').write_text(json.dumps(res, indent=1))
    item.update(status='pass' if ok else 'fail', result={k: res[k] for k in ('covered_pct', 'extra_pct', 'checked_at', 'dpi')})
    review_path.write_text(json.dumps(review, indent=1, ensure_ascii=False))
    print(json.dumps(res, indent=1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
