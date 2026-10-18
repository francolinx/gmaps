"""SOAK TEST tool server: the end-to-end evaluation server (eval/e2e_server.py, the deployed tool
server code with a synthetic scenario airport's chart identifiers) plus a stats file written every 30 s, so the soak runner
can log ledger size without touching the server's HTTP surface. Evaluation only; never part of the runtime.

  GMAPS_MCP_TOKEN=... python3 eval/soak_server.py --airport KATL --run-id soak-... --bind 127.0.0.1 --port 11446 --stats FILE [--llm-fallback]
                    [--history-keep N]   # soak-only override of runtime_config history_keep (LOOP Q2 memory proof)

Binds loopback by default and on its own port: the boot-enabled gmaps-mcp.service on 172.18.0.1:11435 is not touched.
N6: POST /soak/reset (same bearer token, soak runner only) gives the server a fresh ledger and session, so a
'fresh' soak lane replays every scenario on its own ledger; a 'continuous' lane never resets and exercises reservation expiry.
"""
import argparse, json, os, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'eval')]
from e2e_server import EvalTools, gmaps_mcp       # noqa: E402


def stats(tools):
    led = tools.led
    return {'epoch': round(time.time(), 1), 'ledger_now': led.now,
            'ledger': {k: len(getattr(led, k)) for k in ('events', 'reservations', 'warnings', 'notes', 'pending', 'timers')} | {'actors': len(led.actors)}
                      | {'open_warnings': len(led.open_warnings()), 'live_reservations': len(led._active()), 'expiries': led.total('expiries'),
                         'cap_events': led.total('cap_events'),
                         'unverified_reservations': sum(1 for r in led._active() if r['status'] == 'expired_unverified'),
                         'soft_expiries': led.expiry_counts.get('expired_unverified', 0),
                         'hard_drops': led.expiry_counts.get('hard_drop_without_release', 0),
                         'conflict_auto_closed': led.expiry_counts.get('conflict_auto_closed', 0),
                         'open_advisories': sum(1 for w in led.open_warnings() if w['rule_id'] == 'R17'),
                         'open_conflict_level': sum(1 for w in led.open_warnings() if w['severity'] in ('CONFLICT', 'CAUTION')),
                         'cap_evictions': led.cap_counts.get('open_warning_cap_evicted', 0), 'over_cap_admissions': led.cap_counts.get('system_degraded', 0),
                         'history_pruned': dict(led.pruned), 'warnings_total': led.total('warnings'),
                         'degraded': led.health['degraded']},
            'resets': getattr(tools, 'resets', 0), 'expiries_total': getattr(tools, 'expiries_total', 0) + led.total('expiries'),
            'tools': {'events': len(tools.events), 'by_source': len(tools.by_source), 'llm_stats': len(getattr(tools, 'llm_stats', []))},
            'history_keep': tools.cfg.get('history_keep'), 'threads': threading.active_count()}


def reset_session(tools):
    """Fresh ledger and session state (N6 soak harness): what a new run of the tool server would start with."""
    tools.expiries_total = getattr(tools, 'expiries_total', 0) + tools.led.total('expiries')
    tools.led = gmaps_mcp.make_ledger(tools.ap, tools.cfg)
    tools.events, tools.by_source, tools.alerted, tools.llm_events = {}, {}, set(), set()
    tools.clock = {'t': None, 'wall': None}
    tools._logged = (0, 0)
    tools.llm_stats = tools.llm_stats[-200:]
    tools.resets = getattr(tools, 'resets', 0) + 1


def handler(tools, token, log_path):
    base = gmaps_mcp.make_handler(tools, token, log_path)

    class Soak(base):
        def do_POST(self):
            if self.path.rstrip('/') != '/soak/reset':
                return super().do_POST()
            if not self._authorized():
                return self._send(401, {'error': 'unauthorized'}, {'WWW-Authenticate': 'Bearer'})
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            with tools.lock:
                reset_session(tools)
            return self._send(200, {'ok': True, 'resets': tools.resets})
    return Soak


def main():
    a = argparse.ArgumentParser(); a.add_argument('--airport', required=True); a.add_argument('--run-id', required=True)
    a.add_argument('--bind', default='127.0.0.1'); a.add_argument('--port', type=int, required=True)
    a.add_argument('--stats', required=True); a.add_argument('--llm-fallback', action='store_true')
    a.add_argument('--history-keep', type=int, help='soak only: override history_keep for this server (runtime_config is not changed)')
    args = a.parse_args()
    token = os.environ.get('GMAPS_MCP_TOKEN')
    if not token or len(token) < 16:
        sys.exit('set GMAPS_MCP_TOKEN (>= 16 chars)')
    log_path = gmaps_mcp.LOG_DIR / f'mcp_calls_{args.run_id}.jsonl'
    tools = EvalTools(args.airport, args.run_id, llm=args.llm_fallback)
    if args.history_keep:
        tools.cfg = dict(tools.cfg, history_keep=args.history_keep)   # this process only; reset_session reuses tools.cfg
        tools.led = gmaps_mcp.make_ledger(tools.ap, tools.cfg)
    gmaps_mcp.start_clock(tools)

    def writer():
        while True:
            Path(args.stats).write_text(json.dumps(stats(tools)) + '\n')
            time.sleep(30)
    threading.Thread(target=writer, daemon=True).start()
    srv = ThreadingHTTPServer((args.bind, args.port), handler(tools, token, log_path))
    print(f'soak server run_id={args.run_id} airport={tools.airport} http://{args.bind}:{args.port} log={log_path}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
