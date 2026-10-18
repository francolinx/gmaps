"""One review contact sheet per airport: full map, full map with OSM, and every hot-spot view, in
one PNG under 4 MB, with the map footer (FAA source + cycle, display only) and the OSM attribution printed on it.

  python3 tools/airport_map/contact_sheet.py [--icao KATL ...] [--base http://127.0.0.1:8765] [--copy-katl ~/Downloads/gmaps_review]

Renders fresh screenshots of ui_scaffold/airport_map.html (same views and window sizes as map_screens.py) into a temporary
folder, so the tracked review/map_screens/*.png are not rewritten. Writes data/airports/<icao>/review/contact_sheet.png.
Review only: it adds no geometry and changes no map output. Needs the local UI server and google-chrome.
"""
import argparse, json, shutil, subprocess, tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
MAX_BYTES = 4 * 1024 * 1024
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def shoot(chrome, base, icao, view, osm, size, fn):
    url = f'{base}/airport_map.html?icao={icao}&view={",".join(map(str, view))}' + ('&osm=1' if osm else '')
    subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars', f'--window-size={size[0]},{size[1]}',
                    '--virtual-time-budget=8000', f'--screenshot={fn}', url], check=True, capture_output=True, timeout=120)
    return Image.open(fn).convert('RGB')


def wrap(draw, text, f, width):
    lines, cur = [], ''
    for w in text.split():
        if draw.textlength((cur + ' ' + w).strip(), font=f) > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + ' ' + w).strip()
    return lines + [cur]


def sheet(icao, base, chrome, tmp):
    meta = json.loads((REPO / 'data/airports' / icao / 'map/map_meta.json').read_text())
    rv = meta['review_views']
    full = shoot(chrome, base, icao, rv['full'], False, (1600, 1100), tmp / f'{icao}_full.png')
    osm = shoot(chrome, base, icao, rv['full'], True, (1600, 1100), tmp / f'{icao}_full_osm.png')
    hs = [(k, shoot(chrome, base, icao, v, False, (1200, 1000), tmp / f'{icao}_{k}.png')) for k, v in sorted(rv.items()) if k.startswith('hot_spot_')]
    W, pad, title_h = 2400, 16, 34
    half = (W - 3 * pad) // 2
    tiles = [('full', full), ('full, OSM on', osm)]
    cols = 3 if len(hs) > 2 else max(1, len(hs))
    hw = (W - (cols + 1) * pad) // cols if hs else 0
    top_h = round(half * full.height / full.width)
    rows = (len(hs) + cols - 1) // cols
    hh = round(hw * 1000 / 1200) if hs else 0
    f_t, f_s, f_h = font(30), font(20), font(40)
    d0 = ImageDraw.Draw(Image.new('RGB', (10, 10)))
    foot = wrap(d0, meta['footer'] + ' ' + meta.get('osm_attribution', '') + f" OSM data {meta.get('osm_data_timestamp', '')}.", f_s, W - 2 * pad)
    head_h = 70
    H = head_h + title_h + top_h + pad + (rows * (title_h + hh + pad) + (title_h if not hs else 0)) + len(foot) * 26 + 2 * pad
    img = Image.new('RGB', (W, H), 'white'); d = ImageDraw.Draw(img)
    signed = meta.get('sign_off')
    d.text((pad, 14), f"{icao.upper()} review contact sheet · reconstructed display geometry · display only, not for navigation, no predictions"
           + (' · signed off' if signed else ' · NOT signed off'), font=font(34), fill='black')
    y = head_h
    for i, (t, im) in enumerate(tiles):
        x = pad + i * (half + pad)
        d.text((x, y), t, font=f_t, fill='black')
        img.paste(im.resize((half, top_h), Image.LANCZOS), (x, y + title_h))
    y += title_h + top_h + pad
    if not hs:
        d.text((pad, y), 'hot spots: none on this chart (checklist hot_spots not_applicable)', font=f_t, fill='#9a5200'); y += title_h
    for i, (k, im) in enumerate(hs):
        r, c = divmod(i, cols)
        x, yy = pad + c * (hw + pad), y + r * (title_h + hh + pad)
        d.text((x, yy), k.replace('hot_spot_', 'hot spot ').upper().replace('HOT SPOT', 'hot spot'), font=f_t, fill='#9a5200')
        img.paste(im.resize((hw, hh), Image.LANCZOS), (x, yy + title_h))
    y += rows * (title_h + hh + pad)
    for ln in foot:
        d.text((pad, y), ln, font=f_s, fill='#333333'); y += 26
    out = REPO / 'data/airports' / icao / 'review/contact_sheet.png'
    for scale in (1.0, 0.85, 0.7, 0.6, 0.5):   # keep it under 4 MB: shrink, then palette-quantise if needed
        im = img if scale == 1.0 else img.resize((round(W * scale), round(H * scale)), Image.LANCZOS)
        for q in (False, True):
            (im.quantize(256, method=Image.Quantize.MEDIANCUT) if q else im).save(out, optimize=True)
            if out.stat().st_size < MAX_BYTES:
                return out, len(hs), out.stat().st_size, scale, q
    raise SystemExit(f'{icao}: contact sheet still >= 4 MB')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', action='append'); ap.add_argument('--base', default='http://127.0.0.1:8765')
    ap.add_argument('--copy-katl', help='also copy KATL\'s sheet to this folder as katl_contact_sheet.png')
    a = ap.parse_args()
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    icaos = [i.lower() for i in a.icao] if a.icao else sorted(p.parent.parent.name for p in (REPO / 'data/airports').glob('*/map/map_meta.json'))
    with tempfile.TemporaryDirectory() as td:
        for icao in icaos:
            out, n, size, scale, q = sheet(icao, a.base, chrome, Path(td))
            print(f'{out.relative_to(REPO)}  hot spots {n}  {size / 1e6:.2f} MB  scale {scale}{"  palette" if q else ""}')
    if a.copy_katl:
        dst = Path(a.copy_katl).expanduser(); dst.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / 'data/airports/katl/review/contact_sheet.png', dst / 'katl_contact_sheet.png')
        print(f'copied -> {dst / "katl_contact_sheet.png"}')


if __name__ == '__main__':
    main()
