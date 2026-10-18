"""Build ~/gmaps_venue/runtime/ from an explicit allow-list (venue code).

The agent-facing tool server runs ONLY from this directory. It must never contain eval/ (labels, truth),
sim/truth.py, sim/observe.py (imports truth), tools/make_episodes.py, or full observation files.
Observation packets reach runtime/feed/ only through venue/replay_emit.py (received_at <= as_of).
"""
import hashlib, json, shutil, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEST = Path.home() / 'gmaps_venue' / 'runtime'

ALLOW = [
    'lib/gmaps_core/__init__.py', 'lib/gmaps_core/adapters.py', 'lib/gmaps_core/airport.py',
    'lib/gmaps_core/ledger.py', 'lib/gmaps_core/normalize.py', 'lib/gmaps_core/parser.py',
    'lib/gmaps_core/sim/__init__.py', 'lib/gmaps_core/sim/predict.py', 'lib/gmaps_core/sim/simmap.py',
    'data/airports/klga.graph.json', 'data/airports/kbos.graph.json', 'data/airports/identifier_aliases.json',
    'data/specs/phraseology_grammar.json', 'data/specs/runtime_config.json',
    'lib/gmaps_core/traffic.py', 'lib/gmaps_core/asr_conf.py',
    'venue/gmaps_mcp.py', 'venue/llm_fallback.py',
]
FORBIDDEN = ['eval', 'truth', 'observe.py', 'make_episodes', '.obs.json', 'labels']


def main():
    if DEST.exists():
        for p in DEST.iterdir():
            if p.name not in ('feed',):
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    manifest = {}
    for rel in ALLOW:
        src = REPO / rel
        dst = DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest[rel] = hashlib.sha256(src.read_bytes()).hexdigest()
    (DEST / 'feed').mkdir(exist_ok=True)
    (DEST / 'RUNTIME_MANIFEST.json').write_text(json.dumps({'source_repo': str(REPO), 'files': manifest}, indent=1))
    bad = [str(p.relative_to(DEST)) for p in DEST.rglob('*')
           if p.is_file() and 'feed' not in p.parts and any(f in str(p.relative_to(DEST)) for f in FORBIDDEN)]
    if bad:
        sys.exit(f'FORBIDDEN files in runtime: {bad}')
    print(f'runtime built at {DEST}: {len(manifest)} allow-listed files, forbidden check OK')


if __name__ == '__main__':
    main()
