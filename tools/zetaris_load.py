#!/usr/bin/env python3
"""Build the three GMAPS tables for the data layer (Open Agent Hackathon 2026) and load them. Idempotent.

  python3 tools/zetaris_load.py --dry-run     # writes runs/zetaris_tables/<table>.jsonl (row counts printed); no network
  python3 tools/zetaris_load.py               # load into Zetaris: NOT INTEGRATED (ZetarisStore raises NotIntegrated)
Tables: gmaps_identifiers (XSIM + KBOS graph; KLGA never loaded), gmaps_transmissions (SIM_E* scripted lines, fictional),
gmaps_ledger (rows the agent wrote: runs/ledger/*.jsonl). Rows are rewritten whole each run, so re-running is idempotent."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from gmaps_core.data.zetaris_store import identifier_rows, transmission_rows, ZetarisStore, NotIntegrated   # noqa: E402

tables = {'gmaps_identifiers': identifier_rows(), 'gmaps_transmissions': transmission_rows(),
          'gmaps_ledger': [json.loads(l) for p in sorted((ROOT / 'runs/ledger').glob('*.jsonl')) for l in p.read_text().splitlines() if l.strip()]}
if '--dry-run' in sys.argv:
    out = ROOT / 'runs' / 'zetaris_tables'
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        (out / f'{name}.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
        print(f'{name}: {len(rows)} rows -> {(out / name).relative_to(ROOT)}.jsonl')
    sys.exit(0)
try:
    ZetarisStore()
except NotIntegrated as e:
    sys.exit(f'NOT LOADED: {e}')
