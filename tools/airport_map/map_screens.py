"""Review screenshots of the map viewer (ui_scaffold/airport_map.html) at the views stored in map_meta.json.

  python3 tools/airport_map/map_screens.py --icao KLGA [--base http://localhost:8765]

Needs the local UI server (python3 -m http.server 8765 --bind 127.0.0.1, from ui_scaffold/) and google-chrome.
Writes data/airports/<icao>/review/map_screens/*.png. Review only.
"""
import argparse, json, shutil, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--icao', required=True); ap.add_argument('--base', default='http://localhost:8765'); ap.add_argument('--only', action='append', help='file stem(s) to render, e.g. full terminal_b_osm')
    a = ap.parse_args()
    icao = a.icao.lower(); meta = json.loads((REPO / 'data/airports' / icao / 'map/map_meta.json').read_text())
    out = REPO / 'data/airports' / icao / 'review/map_screens'; out.mkdir(parents=True, exist_ok=True)
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    shots = [('full', meta['review_views']['full'], False, (1600, 1100)), ('full_osm_on', meta['review_views']['full'], True, (1600, 1100))]
    shots += [(k, v, osm, (1200, 1000)) for k, v in meta['review_views'].items() if k != 'full' for osm in (False, True)]
    for name, v, osm, (w, h) in shots:
        fn = out / f'{name}{"_osm" if osm and not name.endswith("osm_on") else ""}.png'
        if a.only and fn.stem not in a.only:
            continue
        url = f'{a.base}/airport_map.html?icao={icao}&view={",".join(map(str, v))}' + ('&osm=1' if osm else '')
        subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars', f'--window-size={w},{h}',
                        '--virtual-time-budget=8000', f'--screenshot={fn}', url], check=True, capture_output=True, timeout=120)
        print(fn.relative_to(REPO))


if __name__ == '__main__':
    main()
