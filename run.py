#!/usr/bin/env python3
"""GMAPS entry point (Open Agent Hackathon 2026). SIMULATED: fictional SIM-1 map, scripted or judge-supplied radio text.

  python3 run.py                                   # run the three input_examples, print results, traces in logs/
  python3 run.py --input input_examples/example_2.json
  python3 run.py --serve --port 8000               # HTTP: POST /run with an input JSON, GET /health
  SAMPLE_MODE=true python3 run.py ...              # no Nightshift, no credentials: recorded answers replayed, fallbacks otherwise

Input JSON (one of):
  {"scenario": "SIM_E1_CROSS_DURING_ROLLOUT"}                          a scripted scenario (radio + simulated positions)
  {"transmissions": [{"t": 0, "speaker": "ATC", "text": "..."}, ...],  your own radio lines (speaker ATC or a callsign);
   "scenario": "SIM_E1_CROSS_DURING_ROLLOUT"}                          optional: borrow that scenario's simulated positions
Output JSON: summary (provider, model_reported, model calls, latency), alerts (each with its explanation and which model or
fallback produced it), transcript (how each line was typed), the trace path. Every step is one JSON line in logs/<run_id>.jsonl.
Provider: Meterless Nightshift when NIGHTSHIFT_API_KEY (or ~/.nightshift_key) is set and SAMPLE_MODE is not true.
"""
import argparse, itertools, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT)]
from agent.open_agent_loop import OpenAgent                     # noqa: E402
from gmaps_core.trace import TraceLogger                        # noqa: E402
from gmaps_core.providers import get_provider                   # noqa: E402
from gmaps_core.data import get_store                           # noqa: E402
from gmaps_core.engines import get_runway_memory                # noqa: E402

LOGS = ROOT / 'logs'
_N = itertools.count(1)
SCEN = {p.name.split('.')[0] for p in (ROOT / 'episodes').glob('SIM_E*.transcript.json')}


def provider_name(req):
    if os.environ.get('SAMPLE_MODE', '').lower() in ('1', 'true', 'yes'):
        return 'sample'
    return req.get('provider') or None


def run(req):
    """-> (http status, result dict). Validates the input; never raises to the caller."""
    if not isinstance(req, dict):
        return 400, {'error': 'input must be a JSON object'}
    scen, tx = req.get('scenario'), req.get('transmissions')
    if scen is not None and scen not in SCEN:
        return 400, {'error': f'unknown scenario {scen!r}', 'scenarios': sorted(SCEN)}
    if tx is not None:
        if not isinstance(tx, list) or not tx or not all(isinstance(x, dict) and isinstance(x.get('text'), str) and isinstance(x.get('t'), (int, float)) for x in tx):
            return 400, {'error': 'transmissions must be a non-empty list of {"t": seconds, "speaker": "ATC" | callsign, "text": "..."}'}
        if len(tx) > 50:
            return 400, {'error': 'at most 50 transmissions per run (bounded runtime)'}
    if scen is None and tx is None:
        return 400, {'error': 'give "scenario" or "transmissions"', 'scenarios': sorted(SCEN)}
    try:
        prov = get_provider(provider_name(req), quiet=True)
    except ValueError as e:
        return 400, {'error': str(e)}
    t_end = 100 if tx is None else int(max(x['t'] for x in tx)) + 60
    run_id = f"{scen or 'custom'}-{prov.name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{os.getpid() % 1000:03d}-{next(_N)}"
    tr = TraceLogger(run_id, scenario=scen or 'custom', out_dir=LOGS)
    try:
        out = OpenAgent(scen, prov, get_store(), get_runway_memory(), tr, t_end=t_end, transmissions=tx).run()
    except Exception as e:   # a failure is reported, not a stack trace
        tr.log('error', 'planner', 'escalated', error=f'{type(e).__name__}: {e}')
        return 500, {'error': f'run failed: {type(e).__name__}: {e}', 'trace': str(tr.path.relative_to(ROOT))}
    out['trace'] = str(tr.path.relative_to(ROOT))
    out['input'] = req
    return 200, out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj, indent=1, default=str).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip('/') in ('', '/health'):
            self._send(200, {'ok': True, 'scenarios': sorted(SCEN), 'sample_mode': provider_name({}) == 'sample', 'usage': 'POST /run with an input JSON (see run.py docstring)'})
        else:
            self._send(404, {'error': 'GET /health or POST /run'})

    def do_POST(self):
        if self.path.rstrip('/') != '/run':
            return self._send(404, {'error': 'POST /run'})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        except json.JSONDecodeError as e:
            return self._send(400, {'error': f'invalid JSON: {e}'})
        self._send(*run(req))


def show(out):
    sm = out['summary']
    print(f"{sm['scenario'] or 'custom'}  provider={sm['provider']}  model_reported={sm['model_reported'] or '-'}  model_calls={sm['model_calls']}  alerts={sm['alerts']}  trace={out['trace']}")
    for a in out['alerts']:
        print(f"  t=+{a['t']:>5}  {a['severity']:<10} {a['headline']}\n           {a['explanation']}\n           [explanation: {a['explanation_source']}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', action='append', help='input JSON file (repeatable); default: input_examples/*.json')
    ap.add_argument('--serve', action='store_true')
    ap.add_argument('--host', default=os.environ.get('GMAPS_HOST', '127.0.0.1'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8000)))
    ap.add_argument('--write-outputs', action='store_true', help='write output_examples/<name>.json from this run')
    a = ap.parse_args()
    if a.serve:
        print(f'GMAPS agent on http://{a.host}:{a.port}  (POST /run, GET /health)  sample_mode={provider_name({}) == "sample"}', flush=True)
        ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
    files = a.input or sorted(str(p) for p in (ROOT / 'input_examples').glob('*.json'))
    rc = 0
    for f in files:
        code, out = run(json.loads(Path(f).read_text()))
        if code != 200:
            print(f'{f}: HTTP {code} {out}'); rc = 1; continue
        show(out)
        if a.write_outputs:
            dst = ROOT / 'output_examples' / Path(f).name
            dst.parent.mkdir(exist_ok=True)
            dst.write_text(json.dumps(out, indent=1, default=str) + '\n')
    sys.exit(rc)


if __name__ == '__main__':
    main()
