"""END-TO-END EVALUATION tool server: the deployed GMAPS tool server, loaded with a synthetic scenario's
airport identifiers instead of the fictional SIM-1 ledger. Evaluation only; never part of the runtime.

  GMAPS_MCP_TOKEN=... python3 eval/e2e_server.py --airport KATL --run-id e2e-... --bind 172.18.0.1 --port 11435

Same code paths as venue/gmaps_mcp.py (host fast path, process_transmission, ledger rules, alert-on-new-warning, MCP
over HTTP, tool-call log in ~/gmaps_venue/log/mcp_calls_<run>.jsonl). Differences, all on the safe side:
  * resolver = chart identifiers + topology of the scenario airport (eval/synthetic/chart_ids.py); no positions.
  * the agent's airport argument is the demo's "XSIM" (AGENTS.md is unchanged); it means "the loaded airport" here.
  * look-ahead never predicts: it ticks the ledger and returns prediction_unavailable (no predictions on real geometry).
  * alerts are dry runs labelled SIMULATED EVALUATION; nothing is sent.
It reads scenario text only through the runner's transmissions; never truth or labels.
"""
import argparse, json, os, sys
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'lib'), str(ROOT / 'venue'), str(ROOT / 'eval/synthetic')]
import gmaps_mcp                                  # noqa: E402
from gmaps_core.ledger import Ledger              # noqa: E402
from gmaps_core.parser import parse, classify_speaker   # noqa: E402
from gmaps_core.traffic import match_callsigns    # noqa: E402
from chart_ids import ChartIdentifiers            # noqa: E402


class EvalTools(gmaps_mcp.GmapsTools):
    def __init__(self, airport, run_id, traffic=None, llm=False):
        super().__init__(f'eval-{airport}', run_id, send=False, input_mode='synthetic evaluation (SIMULATED)')
        self.airport = airport.upper()
        self.ap = ChartIdentifiers(self.airport)
        self.led = gmaps_mcp.make_ledger(self.ap, self.cfg)
        self.traffic = traffic
        if llm:
            self.enable_llm(sorted({e['identifier'] for e in self.ap.ends.values()}), sorted(self.ap.vocab))

    def parse_transmission(self, text, airport, source_id=None, t=None, words=None):
        if airport not in (gmaps_mcp.SIM_AIRPORT, self.airport):
            raise gmaps_mcp.ToolError(f'airport {airport!r} not loaded')
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise gmaps_mcp.ToolError('text must be one transmission, 1-500 characters')
        t = float(t) if t is not None else None
        role, why = classify_speaker(text)   # as the deployed server (venue/gmaps_mcp.py)
        ev = parse(text, self.airport, speaker_hint=role, resolver=self.ap, event_time=t, source={'id': source_id or 'unknown', 'start_s': t, 'end_s': None})
        ev['speaker_classifier'] = {'role': role, 'reason': why}
        if self.traffic:
            match_callsigns(ev, self.traffic)
        if words:   # as the deployed server: Whisper word confidence on the runway named
            rw = ev.get('runway') or next((h.get('runway') for h in ev.get('hold_constraints') or [] if h.get('runway')), None)
            ev['runway_confidence'] = gmaps_mcp.runway_confidence(words, rw) if rw else None
        self.events[ev['event_id']] = ev
        return ev

    def lookahead(self, as_of):
        as_of = float(as_of)
        self.led.tick(as_of)
        self.last_lookahead = {'as_of': as_of, 'status': 'prediction_unavailable',
                               'reason': 'synthetic evaluation on real-airport identifiers: no predictions', 'note': 'Prediction unavailable is not "no conflict".'}
        return self.last_lookahead

    def alert_text(self, w):
        a, b = w.get('authorization_a') or {}, w.get('authorization_b') or {}
        return '\n'.join([f"GMAPS SIMULATED EVALUATION ({self.airport} identifiers, synthetic scenario; dry run, not sent)",
                          f"Rule {w['rule_id']} ({w['severity']}): {w['name']} - warning {w['warning_id']} at t={w['t_fired']} s",
                          f"Actors: {a.get('callsign')} {a.get('type')} rwy {a.get('runway_end')} / {b.get('callsign')} {b.get('type')} rwy {b.get('runway_end')}",
                          f"Run {self.run_id}"])


def main():
    a = argparse.ArgumentParser(); a.add_argument('--airport', required=True); a.add_argument('--run-id', required=True)
    a.add_argument('--bind', default='172.18.0.1'); a.add_argument('--port', type=int, default=11435)
    a.add_argument('--traffic', help='traffic list JSON file (callsigns on frequency; ADS-B stand-in)'); a.add_argument('--llm-fallback', action='store_true')
    args = a.parse_args()
    token = os.environ.get('GMAPS_MCP_TOKEN')
    if not token or len(token) < 16:
        sys.exit('set GMAPS_MCP_TOKEN (>= 16 chars)')
    log_path = gmaps_mcp.LOG_DIR / f'mcp_calls_{args.run_id}.jsonl'
    tools = EvalTools(args.airport, args.run_id, traffic=json.loads(Path(args.traffic).read_text()) if args.traffic else None, llm=args.llm_fallback)
    gmaps_mcp.start_clock(tools)
    srv = ThreadingHTTPServer((args.bind, args.port), gmaps_mcp.make_handler(tools, token, log_path))
    print(f'e2e eval server run_id={args.run_id} airport={tools.airport} http://{args.bind}:{args.port}/mcp log={log_path}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
