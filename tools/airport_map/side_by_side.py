"""Visual review aid: the FAA PDF (left) and the map view's chart.svg (right) at the same scale, cut into a grid of
region tiles side by side (red divider). Review only; output goes wherever you point it (keep it out of the repo).

  .venv/bin/python tools/airport_map/side_by_side.py KHWD <out_dir> [dpi=220] [grid=3]
"""
import json, re, shutil, subprocess, sys, tempfile
from pathlib import Path
import numpy as np
from PIL import Image
icao, out = sys.argv[1].lower(), Path(sys.argv[2]); dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 220; grid = int(sys.argv[4]) if len(sys.argv) > 4 else 3
out.mkdir(parents=True, exist_ok=True)
REPO = Path(__file__).resolve().parents[2]; base = REPO / 'data/airports' / icao
meta = json.loads((base / 'build/layers.raw.json').read_text())['meta']
FW, FH = meta['frame_size_pt']; rot = meta['content_rotation_deg']
td = Path(tempfile.mkdtemp(dir=out))
subprocess.run(['pdftoppm', '-r', str(dpi), '-png', '-singlefile', str(REPO / meta['pdf']), str(td / 'faa')], check=True)
faa = np.array(Image.open(td / 'faa.png').convert('RGB'))
if rot == 90: faa = np.transpose(np.flipud(faa), (1, 0, 2))
elif rot == 270: faa = np.transpose(np.fliplr(faa), (1, 0, 2))
elif rot == 180: faa = faa[::-1, ::-1]
ph, pw = faa.shape[:2]
svg = (base / 'map/chart.svg').read_text()
m = re.search(r'transform="matrix\(([^)]*)\)"', svg); a, b, c, d, e, f = map(float, m.group(1).split())
svg = re.sub(r'viewBox="[^"]*"', f'viewBox="{e:.3f} {f:.3f} {a*FW:.3f} {d*FH:.3f}" width="{pw}" height="{ph}" preserveAspectRatio="none"', svg, 1)
svg = svg.replace('preserveAspectRatio="xMidYMid meet"', '', 1)
(td / 'c.svg').write_text(svg)
chrome = shutil.which('google-chrome')
subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
                f'--window-size={pw},{ph}', f'--screenshot={td / "rec.png"}', (td / 'c.svg').as_uri()], check=True, capture_output=True, timeout=120)
rec = np.array(Image.open(td / 'rec.png').convert('RGB'))[:ph, :pw]
for gy in range(grid):
    for gx in range(grid):
        y0, y1, x0, x1 = gy * ph // grid, (gy + 1) * ph // grid, gx * pw // grid, (gx + 1) * pw // grid
        t = np.full((y1 - y0, 2 * (x1 - x0) + 8, 3), 255, np.uint8); t[:, x1 - x0:x1 - x0 + 8] = (200, 0, 0)
        t[:, :x1 - x0] = faa[y0:y1, x0:x1]; t[:, x1 - x0 + 8:] = rec[y0:y1, x0:x1]
        Image.fromarray(t).save(out / f'{icao}_r{gy}{gx}.png')
print(pw, ph)
