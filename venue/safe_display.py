"""Tailnet display: serve a SAFE COPY of ui_scaffold/ with directory listing off.

  python3 venue/safe_display.py --bind 100.74.115.100 --port 8766     # rebuild the copy, then serve it
  python3 venue/safe_display.py --build-only

The copy (~/gmaps_venue/display_safe/, outside the repo) is rebuilt from ui_scaffold/ on every start and never contains
the INTERNAL LGA replay: klga_view.html, klga_review.js, samples/LGA_2026-03-22.json. Links to them are dropped from
nav.html. Directory requests without an index.html get 404 (no listings). The audio server is never proxied: it stays
on 127.0.0.1:8767 and nothing here forwards to it. Stdlib only.
"""
import argparse, functools, http.server, shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'ui_scaffold'
DEST = Path.home() / 'gmaps_venue' / 'display_safe'
EXCLUDE = {'klga_view.html', 'klga_review.js', 'samples/LGA_2026-03-22.json'}


def build():
    tmp = DEST.with_name(DEST.name + '.new')
    if tmp.exists():
        shutil.rmtree(tmp)
    for p in SRC.rglob('*'):
        rel = p.relative_to(SRC).as_posix()
        if p.is_symlink() or rel in EXCLUDE or any(part.startswith('.') for part in p.relative_to(SRC).parts):
            continue
        out = tmp / rel
        if p.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            if rel == 'nav.html':   # drop the links to what is not served
                out.write_text('\n'.join(l for l in p.read_text().splitlines() if not any(x.split('/')[-1] in l for x in EXCLUDE)) + '\n')
            else:
                shutil.copy2(p, out)
    if DEST.exists():
        shutil.rmtree(DEST)
    tmp.rename(DEST)
    leaked = [x for x in EXCLUDE if (DEST / x).exists()]
    if leaked:
        raise SystemExit(f'safe copy contains {leaked}')
    return sum(1 for q in DEST.rglob('*') if q.is_file())


class NoListing(http.server.SimpleHTTPRequestHandler):
    def list_directory(self, path):          # directory listing off
        self.send_error(404, 'Not found'); return None

    def log_message(self, fmt, *args):       # path + status only
        pass


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--bind', default='127.0.0.1'); ap.add_argument('--port', type=int, default=8766)
    ap.add_argument('--build-only', action='store_true'); a = ap.parse_args()
    n = build(); print(f'safe copy: {n} files -> {DEST} (excluded: {sorted(EXCLUDE)})', flush=True)
    if a.build_only:
        return
    srv = http.server.ThreadingHTTPServer((a.bind, a.port), functools.partial(NoListing, directory=str(DEST)))
    print(f'serving {DEST} on http://{a.bind}:{a.port} (no directory listing)', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
