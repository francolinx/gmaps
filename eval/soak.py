"""SOAK TEST: continuous real-time replay of the synthetic scenarios (SIMULATED) through the host
fast path + LLM fallback, agent off, for hours. Measures stability only; nothing is scored and nothing is tuned from it.

  python3 eval/soak.py --hours 3                      # logs to ~/gmaps_venue/log/soak_<ts>/ (outside the repo)
  python3 eval/soak.py --summarize ~/gmaps_venue/log/soak_<ts>   # -> trends JSON (also written at the end of a run)

Set-up:
  * two long-lived soak servers (eval/soak_server.py = the deployed tool server code with the scenario airport's chart
    identifiers, LLM fallback on, no predictions, dry-run alerts), KATL on 127.0.0.1:11446 and KLGA on 127.0.0.1:11447.
    The boot-enabled gmaps-mcp.service (172.18.0.1:11435) and the demo lock are not touched.
  * one replay thread per airport cycles through every synthetic scenario of that airport (all splits), on the scenario
    clock, with a gap of readback_timeout + 5 s between scenarios. Text = the stored Whisper output (with prompt and word
    confidences when present, else plain Whisper, else the script); Whisper itself is not in the loop. Scenario time is the
    soak clock (monotonic for the long-lived ledger); source ids are prefixed per pass. No traffic list.
  * N6, two kinds of lane per airport (--mode both, the default):
      fresh       the runner POSTs /soak/reset before every scenario: one fresh ledger and session per scenario (harness fix);
      continuous  one ledger for the whole soak, never reset: exercises reservation expiry, warning dedup and the open-warning
                  cap (runtime fix). Callsigns repeat across scenarios, so its alert count is not an accuracy figure.
    The 3 h soak ran one continuous ledger per airport WITHOUT expiry.
Every minute: fast-path latency percentiles (all / grammar / LLM-called), server RSS / CPU / threads / fds, vLLM container
CPU and memory (docker stats, read only), GPU temperature / power / utilisation, client errors, server restarts, server log
tracebacks, ledger size. Stops early if the GPU passes --gpu-max-c or errors spike (>= 5 errors and >= 20 % of a minute's
requests, two minutes in a row).
"""
import argparse, json, os, signal, statistics, subprocess, sys, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENUE = Path.home() / 'gmaps_venue'
LANES = {'KATL-fresh': ('KATL', 11446, True), 'KLGA-fresh': ('KLGA', 11447, True),
         'KATL-cont': ('KATL', 11448, False), 'KLGA-cont': ('KLGA', 11449, False)}
SERVERS = LANES   # lane name -> (airport, port, fresh per scenario)
SPLITS = ('dev', 'heldout', 'dev3', 'heldout3', 'dev4', 'heldout4')


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 4)


def scenario_lines(airport):
    out = []
    for split in SPLITS:
        for sp in sorted((ROOT / 'eval/synthetic' / split / airport.lower()).glob('*.scenario.json')):
            sc = json.loads(sp.read_text())
            asr = {}
            for suffix in ('.asr_prompt_words.json', '.asr_prompt.json', '.asr.json'):
                p = sp.with_name(sp.name.replace('.scenario.json', suffix))
                if p.exists():
                    asr = json.loads(p.read_text()); break
            lines = []
            for tx in sorted(sc['transmissions'], key=lambda x: x['t']):
                a = asr.get(tx['id']) or {}
                lines.append({'t': tx['t'], 'id': tx['id'], 'text': a.get('text') or tx['text'], 'words': a.get('words')})
            out.append((split, sc['id'], lines))
    by = {sp: [x[1:] for x in out if x[0] == sp] for sp in SPLITS}   # round-robin over the splits: the noisy sets (LLM calls) from the start
    return [by[sp][i] for i in range(max(map(len, by.values()))) for sp in SPLITS[::-1] if i < len(by[sp])]


class Soak:
    history_keep = None   # soak-only override (--history-keep); None = runtime_config value

    def __init__(self, out, hours, gpu_max, mode='both'):
        self.out, self.hours, self.gpu_max = out, hours, gpu_max
        self.lanes = {k: v for k, v in LANES.items() if mode == 'both' or (mode == 'fresh') == v[2]}
        self.token = (VENUE / 'secrets/mcp_token').read_text().strip()
        self.cfg = json.loads((ROOT / 'data/specs/runtime_config.json').read_text())
        self.t0 = time.time()
        self.stop = threading.Event()
        self.reqs, self.lock = [], threading.Lock()
        self.procs, self.restarts, self.passes = {}, {a: 0 for a in self.lanes}, {a: 0 for a in self.lanes}
        self.stop_reason = None
        self.cpu_prev = {}

    def soak_t(self):
        return round(time.time() - self.t0, 2)

    def start_server(self, ap):
        run_id = f'{self.out.name}-{ap.lower()}-{self.restarts[ap]}'
        cmd = [sys.executable, str(ROOT / 'eval/soak_server.py'), '--airport', self.lanes[ap][0], '--run-id', run_id, '--bind', '127.0.0.1',
               '--port', str(self.lanes[ap][1]), '--stats', str(self.out / f'stats_{ap}.json'), '--llm-fallback'] + (
               ['--history-keep', str(self.history_keep)] if self.history_keep else [])
        p = subprocess.Popen(cmd, env=dict(os.environ, GMAPS_MCP_TOKEN=self.token), stdout=open(self.out / f'server_{ap}.log', 'a'),
                             stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(80):
            try:
                if run_id in urllib.request.urlopen(f'http://127.0.0.1:{self.lanes[ap][1]}/health', timeout=1).read().decode():
                    self.procs[ap] = p; return
            except Exception:
                time.sleep(0.25)
        raise SystemExit(f'soak server {ap} did not start')

    def post(self, ap, line, sid, t):
        body = json.dumps({'text': line['text'], 'airport': 'XSIM', 'source_id': sid, 't': t, **({'words': line['words']} if line['words'] else {})}).encode()
        req = urllib.request.Request(f'http://127.0.0.1:{self.lanes[ap][1]}/transmission', data=body,
                                     headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        e0, t0 = time.time(), time.perf_counter()
        rec = {'ap': ap, 'epoch': e0}
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
            rec.update(ok=True, grammar=bool(res.get('grammar')), llm=bool(res.get('llm_parse') or res.get('llm')), alerts=len(res.get('alerts') or []))
        except Exception as ex:
            rec.update(ok=False, error=f'{type(ex).__name__}: {str(ex)[:120]}')
        rec['s'] = round(time.perf_counter() - t0, 4)
        with self.lock:
            self.reqs.append(rec)

    def reset(self, ap):
        req = urllib.request.Request(f'http://127.0.0.1:{self.lanes[ap][1]}/soak/reset', data=b'{}',
                                     headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except Exception as ex:
            with self.lock:
                self.reqs.append({'ap': ap, 'epoch': time.time(), 'ok': False, 'error': f'reset {type(ex).__name__}: {str(ex)[:100]}', 's': 0})

    def replay(self, ap):
        scs = scenario_lines(self.lanes[ap][0])
        gap = self.cfg['readback_timeout_s'] + 5
        while not self.stop.is_set():
            n = self.passes[ap]
            for sc_id, lines in scs:
                if self.lanes[ap][2]:
                    self.reset(ap)
                base = time.time()
                for ln in lines:
                    if self.stop.wait(max(0.0, base + ln['t'] - time.time())):
                        return
                    self.post(ap, ln, f'P{n}-{ln["id"]}', self.soak_t())
                if self.stop.wait(gap):
                    return
            self.passes[ap] += 1

    def proc_stats(self, pid):
        try:
            st = Path(f'/proc/{pid}/stat').read_text().split(')')[1].split()
            cpu_s = (int(st[11]) + int(st[12])) / os.sysconf('SC_CLK_TCK')
            rss = int(next(l for l in Path(f'/proc/{pid}/status').read_text().splitlines() if l.startswith('VmRSS')).split()[1]) / 1024
            thr = int(st[17]); fds = len(os.listdir(f'/proc/{pid}/fd'))
        except Exception:
            return None
        now = time.time(); prev = self.cpu_prev.get(pid); self.cpu_prev[pid] = (now, cpu_s)
        return {'rss_mb': round(rss, 1), 'cpu_pct': round(100 * (cpu_s - prev[1]) / (now - prev[0]), 1) if prev else None, 'threads': thr, 'fds': fds}

    @staticmethod
    def gpu():
        try:
            o = subprocess.run(['nvidia-smi', '--query-gpu=temperature.gpu,power.draw,utilization.gpu', '--format=csv,noheader,nounits'],
                               capture_output=True, text=True, timeout=20).stdout.strip().split(', ')
            return {'temp_c': float(o[0]), 'power_w': float(o[1]), 'util_pct': float(o[2])}
        except Exception as ex:
            return {'error': str(ex)[:80]}

    @staticmethod
    def vllm():
        try:
            o = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{json .}}', 'nemoclaw-vllm'], capture_output=True, text=True, timeout=30).stdout
            d = json.loads(o); return {'cpu': d.get('CPUPerc'), 'mem': d.get('MemUsage')}
        except Exception as ex:
            return {'error': str(ex)[:80]}

    def minute(self, i, last):
        now = time.time()
        with self.lock:
            win = [r for r in self.reqs if last <= r['epoch'] < now]
        ok = [r for r in win if r['ok']]
        lat = lambda rs: {str(p): pct([r['s'] for r in rs], p) for p in (50, 95, 99)} | {'max': max((r['s'] for r in rs), default=None), 'n': len(rs)}
        by_lane = {ap: {'grammar': lat([r for r in ok if r['ap'] == ap and r['grammar']]), 'llm_called': lat([r for r in ok if r['ap'] == ap and r['llm'] and not r['grammar']])} for ap in self.lanes}
        row = {'minute': i, 'epoch': round(now, 1), 'soak_min': round((now - self.t0) / 60, 2), 'requests': len(win), 'errors': len(win) - len(ok),
               'error_samples': [r['error'] for r in win if not r['ok']][:3],
               'latency_s': {'all': lat(ok), 'grammar': lat([r for r in ok if r['grammar']]), 'llm_called': lat([r for r in ok if r['llm'] and not r['grammar']])},
               'latency_by_lane_s': by_lane, 'alerts': sum(r.get('alerts', 0) for r in ok), 'gpu': self.gpu(), 'vllm_container': self.vllm(), 'servers': {}}
        for ap, p in self.procs.items():
            alive = p.poll() is None
            if not alive:   # crash: count it, restart, keep going (the restart is part of what the soak measures)
                self.restarts[ap] += 1; self.start_server(ap)
            log = (self.out / f'server_{ap}.log').read_text(errors='replace')
            try:
                st = json.loads((self.out / f'stats_{ap}.json').read_text())
            except Exception:
                st = None
            row['servers'][ap] = {'alive_at_sample': alive, 'restarts': self.restarts[ap], 'passes': self.passes[ap], 'proc': self.proc_stats(self.procs[ap].pid),
                                  'tracebacks': log.count('Traceback'), 'log_mb': round(len(log) / 1e6, 2), 'stats': st,
                                  'call_log_mb': round(sum(f.stat().st_size for f in (VENUE / 'log').glob(f'mcp_calls_{self.out.name}-{ap.lower()}-*.jsonl')) / 1e6, 2),
                                  'call_log_files': len(list((VENUE / 'log').glob(f'mcp_calls_{self.out.name}-{ap.lower()}-*.jsonl')))}
        with open(self.out / 'minutes.jsonl', 'a') as f:
            f.write(json.dumps(row) + '\n')
        return row

    def run(self):
        for ap in self.lanes:
            self.start_server(ap)
        ths = [threading.Thread(target=self.replay, args=(ap,), daemon=True) for ap in self.lanes]
        for th in ths:
            th.start()
        spikes, last, i = 0, time.time(), 0
        end = self.t0 + self.hours * 3600
        try:
            while time.time() < end:
                time.sleep(max(0.0, min(60.0, end - time.time())))
                i += 1; row = self.minute(i, last); last = row['epoch']
                g = row['gpu'].get('temp_c')
                print(f"min {i:3} req {row['requests']:3} err {row['errors']} p95 {row['latency_s']['all']['95']} gpu {g}C {row['gpu'].get('power_w')}W "
                      + ' '.join(f"{ap} rss {s['proc'] and s['proc']['rss_mb']} led {s['stats'] and s['stats']['ledger']['events']}" for ap, s in row['servers'].items()), flush=True)
                if g is not None and g > self.gpu_max:
                    self.stop_reason = f'GPU temperature {g} C > {self.gpu_max} C at minute {i}'; break
                spikes = spikes + 1 if row['errors'] >= 5 and row['errors'] >= 0.2 * max(1, row['requests']) else 0
                if spikes >= 2:
                    self.stop_reason = f'error spike at minutes {i - 1}-{i}'; break
        finally:
            self.stop.set()
            for p in self.procs.values():
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        (self.out / 'end.json').write_text(json.dumps({'stop_reason': self.stop_reason or 'duration reached', 'minutes': i, 'passes': self.passes, 'restarts': self.restarts,
                                                       'lanes': {k: {'airport': v[0], 'port': v[1], 'fresh_per_scenario': v[2]} for k, v in self.lanes.items()}}) + '\n')
        return summarize(self.out)


def slope(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(pts) < 3:
        return None
    mx, my = statistics.fmean(p[0] for p in pts), statistics.fmean(p[1] for p in pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return round(sum((p[0] - mx) * (p[1] - my) for p in pts) / den, 4) if den else None


def summarize(out):
    out = Path(out).expanduser()
    rows = [json.loads(x) for x in (out / 'minutes.jsonl').read_text().splitlines() if x.strip()]
    end = json.loads((out / 'end.json').read_text()) if (out / 'end.json').exists() else {}
    hrs = [r['soak_min'] / 60 for r in rows]
    third = max(1, len(rows) // 3)
    first, lastr = rows[:third], rows[-third:]
    med = lambda rs, k, p: (lambda v: round(statistics.median(v), 4) if v else None)([r['latency_s'][k][p] for r in rs if r['latency_s'][k][p] is not None])
    s = {'note': 'SOAK TEST, SIMULATED synthetic scenarios, fast path + LLM fallback, agent off. Stability only; nothing scored or tuned.',
         'args': json.loads((out / 'soak_args.json').read_text()) if (out / 'soak_args.json').exists() else None,
         'log_dir': str(out).replace(str(Path.home()), '~'), 'minutes': len(rows), 'hours': round(hrs[-1], 2) if hrs else 0,
         'stop_reason': end.get('stop_reason'), 'passes': end.get('passes'), 'restarts': end.get('restarts'),
         'requests': sum(r['requests'] for r in rows), 'errors': sum(r['errors'] for r in rows),
         'latency_s': {k: {'first_third_median_p50': med(first, k, '50'), 'last_third_median_p50': med(lastr, k, '50'),
                           'first_third_median_p95': med(first, k, '95'), 'last_third_median_p95': med(lastr, k, '95'),
                           'overall_max': max((r['latency_s'][k]['max'] for r in rows if r['latency_s'][k]['max'] is not None), default=None)}
                       for k in ('all', 'grammar', 'llm_called')},
         'gpu': {'temp_max_c': max((r['gpu'].get('temp_c') or 0 for r in rows), default=None), 'temp_median_c': statistics.median([r['gpu']['temp_c'] for r in rows if 'temp_c' in r['gpu']]) if rows else None,
                 'power_max_w': max((r['gpu'].get('power_w') or 0 for r in rows), default=None), 'temp_slope_c_per_h': slope(hrs, [r['gpu'].get('temp_c') for r in rows])},
         'servers': {}}
    lanes = list(end.get('lanes') or (rows[-1]['servers'] if rows else []))
    for ap in lanes:
        rss = [(r['servers'].get(ap) or {}).get('proc') and r['servers'][ap]['proc']['rss_mb'] for r in rows]
        led = [((r['servers'].get(ap) or {}).get('stats') or {}).get('ledger', {}).get('events') for r in rows]
        warm = third   # leak estimate after the first third (imports, caches and the LLM client warm up first)
        s['servers'][ap] = {'rss_mb_first': next((x for x in rss if x), None), 'rss_mb_last': next((x for x in reversed(rss) if x), None),
                            'rss_slope_mb_per_h_after_warmup': slope(hrs[warm:], rss[warm:]),
                            'cpu_pct_median': statistics.median([r['servers'][ap]['proc']['cpu_pct'] for r in rows if (r['servers'].get(ap) or {}).get('proc') and r['servers'][ap]['proc']['cpu_pct'] is not None] or [0]),
                            'threads_max': max(((r['servers'].get(ap) or {}).get('proc') or {}).get('threads', 0) for r in rows),
                            'fds_first_last': [next((r['servers'][ap]['proc']['fds'] for r in rows if (r['servers'].get(ap) or {}).get('proc')), None),
                                               next((r['servers'][ap]['proc']['fds'] for r in reversed(rows) if (r['servers'].get(ap) or {}).get('proc')), None)],
                            'ledger_events_last': next((x for x in reversed(led) if x is not None), None), 'ledger_events_per_h': slope(hrs, led),
                            'tracebacks': max((r['servers'].get(ap) or {}).get('tracebacks', 0) for r in rows),
                            'call_log_mb_last': (rows[-1]['servers'].get(ap) or {}).get('call_log_mb') if rows else None}
        lst = lambda k: [((r['servers'].get(ap) or {}).get('stats') or {}).get('ledger', {}).get(k) for r in rows]
        lane_lat = lambda part, p: [((r.get('latency_by_lane_s') or {}).get(ap) or {}).get(part, {}).get(p) for r in rows]
        g95 = lane_lat('grammar', '95')
        m = lambda v: (lambda x: round(statistics.median(x), 4) if x else None)([y for y in v if y is not None])
        s['servers'][ap].update({
            'open_warnings_max': max((x for x in lst('open_warnings') if x is not None), default=None), 'open_warnings_last': next((x for x in reversed(lst('open_warnings')) if x is not None), None),
            'open_warnings_slope_per_h_after_warmup': slope(hrs[warm:], lst('open_warnings')[warm:]),
            'live_reservations_max': max((x for x in lst('live_reservations') if x is not None), default=None),
            'expiries_last': next((((r['servers'].get(ap) or {}).get('stats') or {}).get('expiries_total') for r in reversed(rows) if ((r['servers'].get(ap) or {}).get('stats') or {}).get('expiries_total') is not None), None),
            'cap_events_max': max((x for x in lst('cap_events') if x is not None), default=None),
            'resets_last': next((((r['servers'].get(ap) or {}).get('stats') or {}).get('resets') for r in reversed(rows) if (r['servers'].get(ap) or {}).get('stats')), None),
            'grammar_p95_s_first_third_median': m(g95[:third]), 'grammar_p95_s_last_third_median': m(g95[-third:]), 'grammar_p95_slope_s_per_h': slope(hrs, g95),
            'llm_p95_s_first_third_median': m(lane_lat('llm_called', '95')[:third]), 'llm_p95_s_last_third_median': m(lane_lat('llm_called', '95')[-third:]),
            # N7: soft-expired reservations stay until a release or the 20 min hard limit, so a continuous lane fills for ~20 min;
            # "flat" is judged on the second half of the run
            'open_warnings_slope_per_h_last_half': slope(hrs[len(rows) // 2:], lst('open_warnings')[len(rows) // 2:]),
            'rss_slope_mb_per_h_last_half': slope(hrs[len(rows) // 2:], rss[len(rows) // 2:]),
            'grammar_p95_slope_s_per_h_last_half': slope(hrs[len(rows) // 2:], g95[len(rows) // 2:]),
            'unverified_reservations_max': max((x for x in lst('unverified_reservations') if x is not None), default=None),
            'open_advisories_max': max((x for x in lst('open_advisories') if x is not None), default=None),
            'soft_expiries_last': next((x for x in reversed(lst('soft_expiries')) if x is not None), None),
            'hard_drops_last': next((x for x in reversed(lst('hard_drops')) if x is not None), None),
            'degraded_any': any(lst('degraded')),
            'open_conflict_level_max': max((x for x in lst('open_conflict_level') if x is not None), default=None),
            'conflict_auto_closed_last': next((x for x in reversed(lst('conflict_auto_closed')) if x is not None), None),
            'cap_evictions_last': next((x for x in reversed(lst('cap_evictions')) if x is not None), None),
            'over_cap_admissions_last': next((x for x in reversed(lst('over_cap_admissions')) if x is not None), None),
            'warnings_total_last': next((x for x in reversed(lst('warnings_total')) if x is not None), None),
            'history_pruned_last': next((x for x in reversed(lst('history_pruned')) if x is not None), None)})
    (out / 'summary.json').write_text(json.dumps(s, indent=1) + '\n')
    return s


def main():
    a = argparse.ArgumentParser(); a.add_argument('--hours', type=float, default=3.0); a.add_argument('--gpu-max-c', type=float, default=85.0)
    a.add_argument('--summarize'); a.add_argument('--mode', choices=('both', 'fresh', 'continuous'), default='both')
    a.add_argument('--history-keep', type=int, help='soak servers only: override runtime_config history_keep (LOOP Q2 memory proof)')
    args = a.parse_args()
    if args.summarize:
        print(json.dumps(summarize(args.summarize), indent=1)); return
    out = VENUE / 'log' / f"soak_{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True)
    print(f'soak log: {out}', flush=True)
    sk = Soak(out, args.hours, args.gpu_max_c, args.mode)
    sk.history_keep = args.history_keep
    (out / 'soak_args.json').write_text(json.dumps(vars(args)) + '\n')
    print(json.dumps(sk.run(), indent=1))


if __name__ == '__main__':
    main()
