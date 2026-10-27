#!/usr/bin/env python3
"""Print a GMAPS trace (runs/traces/<run_id>.jsonl) as a table: which role and which model handled each step.
usage: python3 tools/show_trace.py <trace.jsonl> [--calls]   (--calls: model calls only)"""
import json, sys
from pathlib import Path

if len(sys.argv) < 2:
    sys.exit(__doc__)
rows = [json.loads(l) for l in Path(sys.argv[1]).read_text().splitlines() if l.strip()]
if '--calls' in sys.argv:
    rows = [r for r in rows if r.get('provider') and r['step'] not in ('start', 'end')]
print(f"{'seq':>4} {'t':>4}  {'role':<10} {'step':<20} {'outcome':<11} {'provider':<10} {'model_reported':<40} {'ms':>7} {'tok in/out':>10}  grounding / detail")
for r in rows:
    tok = f"{r.get('prompt_tokens') or ''}/{r.get('completion_tokens') or ''}" if r.get('prompt_tokens') is not None else ''
    det = r.get('grounding') or r.get('error') or r.get('status') or r.get('headline') or r.get('reason') or ''
    if r.get('replayed_from'):
        det = f"{det} (replayed from {r['replayed_from']})"
    print(f"{r['seq']:>4} {str(r.get('t') if r.get('t') is not None else ''):>4}  {r['role']:<10} {r['step']:<20} {r['outcome']:<11} "
          f"{(r.get('provider') or ''):<10} {(r.get('model_reported') or ''):<40} {str(r.get('latency_ms') or ''):>7} {tok:>10}  {str(det)[:90]}")
