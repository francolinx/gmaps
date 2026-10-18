"""Accelerated continuous-lane soak (N12 check): the soak's synthetic scenarios replayed back to back on ONE session
that never resets, on a simulated clock (no sleeping), grammar fast path only (no LLM), in-process. Shows that memory levels
off once the history bounds (history_keep) are reached, which a 1 h wall-clock soak does not reach. SIMULATED; evaluation only.

  python3 eval/soak_accel.py KLGA 12 [--out FILE]     # airport, simulated hours
"""
import argparse, contextlib, gc, io, json, sys, tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'eval')]
from soak import scenario_lines          # noqa: E402
from e2e_server import EvalTools         # noqa: E402


def main():
    a = argparse.ArgumentParser(); a.add_argument('airport'); a.add_argument('hours', type=float); a.add_argument('--out')
    args = a.parse_args()
    tools = EvalTools(args.airport, 'accel-' + args.airport, llm=False)
    n_log = [0]
    tools.on_log = lambda r: n_log.__setitem__(0, n_log[0] + 1)   # counted, not written (the call log is on disk anyway)
    tools.on_alert = lambda r: None
    scs, gap = scenario_lines(args.airport), tools.cfg['readback_timeout_s'] + 5
    t, nxt, n, rows, end = 0.0, 0.0, 0, [], args.hours * 3600
    tracemalloc.start()
    with contextlib.redirect_stderr(io.StringIO()) as err:
        while t < end:
            for sc_id, lines in scs:
                base = t
                for ln in lines:
                    t = base + ln['t']; n += 1
                    tools.advance(t)
                    tools.fast_path(ln['text'], 'XSIM', f'{sc_id}-{ln["id"]}-{n}', t, ln['words'])
                t += gap
                tools.advance(t)
                err.seek(0); err.truncate()
                if t >= nxt:
                    gc.collect(); L = tools.led
                    rows.append({'sim_h': round(t / 3600, 2), 'traced_mb': round(tracemalloc.get_traced_memory()[0] / 2**20, 2),
                                 'transmissions': n, 'warnings_in_memory': len(L.warnings), 'open_warnings': len(L.open_warnings()),
                                 'reservations_in_memory': len(L.reservations), 'notes': len(L.notes), 'events': len(L.events),
                                 'actors': len(L.actors), 'session_events': len(tools.events), 'by_source': len(tools.by_source),
                                 'warnings_total': L.total('warnings'), 'conflict_auto_closed': L.expiry_counts.get('conflict_auto_closed', 0),
                                 'cap_evictions': L.cap_counts.get('open_warning_cap_evicted', 0), 'degraded': L.health['degraded'],
                                 'pruned': dict(L.pruned)})
                    print(json.dumps(rows[-1]), flush=True)
                    nxt += 1800
                if t >= end:
                    break
    late = [r['traced_mb'] for r in rows if r['sim_h'] >= args.hours * 0.75]
    out = {'note': 'SIMULATED accelerated soak: synthetic scenarios, simulated clock, grammar fast path only, one session never reset.',
           'airport': args.airport, 'sim_hours': args.hours, 'transmissions': n, 'log_records': n_log[0],
           'traced_mb_last_quarter_min_max': [min(late), max(late)] if late else None, 'rows': rows}
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'rows'}))


if __name__ == '__main__':
    main()
