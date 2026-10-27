#!/usr/bin/env python3
"""Local-execution proof (Open Agent Hackathon 2026): run a command and record what it touched on the network and the GPU.

  python3 tools/local_proof.py -- demo/run_open_agent.py --scenario all --provider nightshift
  python3 tools/local_proof.py --interval 0.5 -- <any command>

While the command runs, every --interval seconds it records:
  * NETWORK, exact: an auditor (tools/net_audit/sitecustomize.py, put on PYTHONPATH for this run only) logs every socket
    connect() of every Python process of the run, so short-lived HTTP calls cannot be missed between samples.
  * NETWORK, process tree: every TCP connection owned by the command and its children (/proc/<pid>/fd -> /proc/net/tcp{,6}).
    Verdict PASS only if every remote address is loopback (127.0.0.0/8, ::1).
  * NETWORK, whole machine: every ESTABLISHED TCP connection to a non-loopback address, with the owning process when
    readable (run with sudo to see other users' processes). This is what would show Nightshift or vLLM calling a cloud
    API during the run. Nothing is hidden: the list is written out in full.
  * GPU: utilization %, memory used, power draw from `nvidia-smi` (the DGX Spark GB10). Missing nvidia-smi is recorded, not faked.
  * Model calls: the trace lines written during the run (runs/traces/*.jsonl, provider / model_reported / latency), drawn as
    markers on the GPU chart so the spikes can be checked against each call.
Output: runs/proof/local_<ts>.json and runs/proof/local_<ts>.html (self-contained, inline SVG). Stdlib only, Linux /proc.
"""
import argparse, glob, html, ipaddress, json, os, shutil, socket, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '06': 'TIME_WAIT', '08': 'CLOSE_WAIT', '0A': 'LISTEN'}


def _addr(hexaddr, v6):
    ip, port = hexaddr.split(':')
    if v6:
        b = bytes.fromhex(ip)
        b = b''.join(b[i:i + 4][::-1] for i in range(0, 16, 4))
        a = ipaddress.IPv6Address(b)
        a = a.ipv4_mapped or a
    else:
        a = ipaddress.IPv4Address(bytes.fromhex(ip)[::-1])
    return str(a), int(port, 16)


def tcp_table():
    """inode -> (local, remote, state) for every TCP socket in this network namespace."""
    out = {}
    for path, v6 in (('/proc/net/tcp', False), ('/proc/net/tcp6', True)):
        try:
            lines = Path(path).read_text().splitlines()[1:]
        except OSError:
            continue
        for l in lines:
            f = l.split()
            out[f[9]] = (_addr(f[1], v6), _addr(f[2], v6), STATES.get(f[3], f[3]))
    return out


def socket_inodes(pid):
    inodes = set()
    for fd in glob.glob(f'/proc/{pid}/fd/*'):
        try:
            t = os.readlink(fd)
        except OSError:
            continue
        if t.startswith('socket:['):
            inodes.add(t[8:-1])
    return inodes


def children(pid):
    kids, todo = set(), [pid]
    while todo:
        p = todo.pop()
        for c in glob.glob(f'/proc/{p}/task/*/children'):
            try:
                for k in Path(c).read_text().split():
                    if int(k) not in kids:
                        kids.add(int(k)); todo.append(int(k))
            except OSError:
                pass
    return kids


def proc_name(pid):
    try:
        return Path(f'/proc/{pid}/comm').read_text().strip()
    except OSError:
        return '?'


def owners():
    """inode -> (pid, name) for every process whose fds are readable."""
    m = {}
    for d in glob.glob('/proc/[0-9]*'):
        pid = int(d.rsplit('/', 1)[1])
        for i in socket_inodes(pid):
            m.setdefault(i, (pid, proc_name(pid)))
    return m


def is_local(ip):
    a = ipaddress.ip_address(ip)
    return a.is_loopback or a.is_unspecified


def gpu_sample():
    if not shutil.which('nvidia-smi'):
        return None
    try:
        r = subprocess.run(['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.used,memory.total,power.draw',
                            '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
        name, util, mu, mt, pw = [x.strip() for x in r.stdout.strip().splitlines()[0].split(',')]
        num = lambda v: float(v) if v.replace('.', '', 1).isdigit() else None   # GB10 may report [N/A] for some fields
        return {'name': name, 'util': num(util), 'mem_used_mib': num(mu), 'mem_total_mib': num(mt), 'power_w': num(pw)}
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}


def chart(series, key, title, unit, t0, t1, marks, color):
    W, H, L, B = 900, 180, 50, 24
    pts = [(s['t'], s['gpu'][key]) for s in series if s.get('gpu') and s['gpu'].get(key) is not None]
    if not pts:
        return f'<p class="na">{html.escape(title)}: not available on this machine (no nvidia-smi value)</p>'
    vmax = max(1.0, max(v for _, v in pts)) * 1.1
    X = lambda t: L + (t - t0) / max(1e-6, t1 - t0) * (W - L - 10)
    Y = lambda v: H - B - v / vmax * (H - B - 10)
    path = ' '.join(f'{"M" if i == 0 else "L"}{X(t):.1f},{Y(v):.1f}' for i, (t, v) in enumerate(pts))
    mk = ''.join(f'<line x1="{X(m["t"]):.1f}" x2="{X(m["t"]):.1f}" y1="8" y2="{H - B}" stroke="#fb923c" stroke-dasharray="3 3"><title>{html.escape(m["label"])}</title></line>' for m in marks)
    return (f'<h3>{html.escape(title)}</h3><svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}">'
            f'<rect x="{L}" y="8" width="{W - L - 10}" height="{H - B - 8}" fill="none" stroke="#2a3140"/>{mk}'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'<text x="{L - 6}" y="16" text-anchor="end">{vmax:.0f}{unit}</text><text x="{L - 6}" y="{H - B}" text-anchor="end">0</text>'
            f'<text x="{L}" y="{H - 6}">0 s</text><text x="{W - 10}" y="{H - 6}" text-anchor="end">{t1 - t0:.0f} s</text></svg>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=float, default=0.5)
    ap.add_argument('cmd', nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ['--'] else a.cmd
    if not cmd:
        sys.exit(__doc__)
    if cmd[0].endswith('.py'):
        cmd = [sys.executable] + cmd
    traces_before = {p: p.stat().st_size for p in (ROOT / 'runs/traces').glob('*.jsonl')} if (ROOT / 'runs/traces').exists() else {}
    t0 = time.time()
    out = ROOT / 'runs' / 'proof'
    out.mkdir(parents=True, exist_ok=True)
    audit = out / f'connects_{time.strftime("%Y%m%d-%H%M%S", time.gmtime(t0))}.jsonl'
    env = dict(os.environ, GMAPS_NET_AUDIT=str(audit), PYTHONPATH=os.pathsep.join([str(ROOT / 'tools/net_audit')] + ([os.environ['PYTHONPATH']] if os.environ.get('PYTHONPATH') else [])))
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
    tree_conns, machine_remote, series = {}, {}, []
    while True:
        done = proc.poll() is not None
        now = time.time()
        table = tcp_table()
        pids = {proc.pid} | children(proc.pid) if not done else set()
        for pid in pids:
            for ino in socket_inodes(pid):
                if ino in table:
                    (lip, lp), (rip, rp), st = table[ino]
                    if rip in ('0.0.0.0', '::') and st == 'LISTEN':
                        continue
                    k = f'{rip}:{rp}'
                    c = tree_conns.setdefault(k, {'remote': k, 'local_only': is_local(rip), 'first_t': round(now - t0, 2), 'process': proc_name(pid), 'states': []})
                    if st not in c['states']:
                        c['states'].append(st)
        own = None
        for ino, ((lip, lp), (rip, rp), st) in table.items():
            if st == 'ESTABLISHED' and not is_local(rip):
                k = f'{rip}:{rp}'
                if k not in machine_remote:
                    own = own if own is not None else owners()
                    pid, name = own.get(ino, (None, 'unknown (not readable; run with sudo)'))
                    machine_remote[k] = {'remote': k, 'local': f'{lip}:{lp}', 'pid': pid, 'process': name, 'first_t': round(now - t0, 2)}
        series.append({'t': now, 'gpu': gpu_sample()})
        if done:
            break
        time.sleep(a.interval)
    t1 = time.time()
    calls = []
    for p in (ROOT / 'runs/traces').glob('*.jsonl') if (ROOT / 'runs/traces').exists() else []:
        data = p.read_bytes()[traces_before.get(p, 0):].decode(errors='replace')
        for l in data.splitlines():
            try:
                r = json.loads(l)
            except json.JSONDecodeError:
                continue
            if r.get('provider') and r.get('step') not in ('start', 'end') and r['ts'] >= t0 - 1:
                calls.append({'t': r['ts'], 'step': r['step'], 'role': r['role'], 'provider': r['provider'], 'model_reported': r.get('model_reported'),
                              'latency_ms': r.get('latency_ms'), 'outcome': r['outcome'], 'file': str(p.relative_to(ROOT)),
                              'label': f"{r['role']}/{r['step']} · {r['provider']} · {r.get('model_reported') or '-'} · {r.get('latency_ms') or '-'} ms"})
    connects = [json.loads(l) for l in audit.read_text().splitlines()] if audit.exists() else []
    for c in connects:
        if c.get('family') == 'AF_UNIX':
            c['local_only'] = True
            continue
        try:
            ips = {ai[4][0] for ai in socket.getaddrinfo(c['host'], c['port'] or 0, proto=socket.IPPROTO_TCP)}
        except OSError:
            ips = {c['host']}
        c['resolved'] = sorted(ips)
        c['local_only'] = all(is_local(i) for i in ips)
    tree_ok = all(c['local_only'] for c in tree_conns.values()) and all(c['local_only'] for c in connects)
    gpus = [s['gpu'] for s in series if s.get('gpu') and 'util' in s['gpu']]
    verdict = {'process_tree_loopback_only': tree_ok,
               'python_connect_calls': len(connects), 'python_connect_targets': sorted({f"{c['host']}:{c['port']}" for c in connects}),
               'process_tree_remote_endpoints': sorted(tree_conns),
               'machine_non_loopback_established': len(machine_remote),
               'gpu': (gpus[0]['name'] if gpus else 'nvidia-smi not available'),
               'gpu_util_max': max((g['util'] for g in gpus if g['util'] is not None), default=None),
               'model_calls_in_window': len(calls), 'models_reported': sorted({c['model_reported'] for c in calls if c['model_reported']})}
    ts = time.strftime('%Y%m%d-%H%M%S', time.gmtime(t0))
    rec = {'command': cmd, 'host': socket.gethostname(), 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t0)),
           'duration_s': round(t1 - t0, 1), 'exit_code': proc.returncode, 'interval_s': a.interval, 'verdict': verdict,
           'python_connect_calls': connects, 'process_tree_connections': list(tree_conns.values()), 'machine_non_loopback': list(machine_remote.values()),
           'model_calls': calls, 'gpu_samples': [{'t': round(s['t'] - t0, 2), **(s['gpu'] or {})} for s in series]}
    (out / f'local_{ts}.json').write_text(json.dumps(rec, indent=1) + '\n')
    rows = lambda items, cols: ''.join('<tr>' + ''.join(f'<td>{html.escape(str(i.get(c, "")))}</td>' for c in cols) + '</tr>' for i in items) or f'<tr><td colspan="{len(cols)}">none</td></tr>'
    badge = ('<span class="ok">PASS · every connection of the GMAPS run went to loopback (this machine): ' + str(len(connects)) + ' connect() calls audited</span>' if tree_ok and connects
             else '<span class="ok">PASS · no network connections at all (sample mode)</span>' if tree_ok
             else '<span class="bad">CHECK · the GMAPS run connected to a non-loopback address (listed below)</span>')
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Local execution proof</title>
<style>:root{{--bg:#0f1115;--ink:#eef1f5;--dim:#a3adbd;--line:#2a3140}}body{{margin:0;padding:16px;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif;max-width:1000px}}
h1{{font-size:18px}}h2{{font-size:15px;margin-top:22px}}h3{{font-size:13px;color:var(--dim);margin:14px 0 4px}}svg{{width:100%;height:auto;background:#0b0e13}}svg text{{fill:var(--dim);font:11px monospace}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}td,th{{border-bottom:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}}.ok{{color:#34d399;font-weight:700}}.bad{{color:#ff4d4f;font-weight:700}}.na{{color:var(--dim)}}code{{font-size:12px}}</style></head><body>
<h1>GMAPS · local execution proof</h1>
<p>Host <b>{html.escape(rec['host'])}</b> · GPU <b>{html.escape(verdict['gpu'])}</b> · started {rec['started_utc']} · {rec['duration_s']} s · exit {rec['exit_code']}<br>Command <code>{html.escape(' '.join(cmd))}</code></p>
<p>{badge}</p>
<p>Model calls in the window: <b>{len(calls)}</b> · models reported: <b>{html.escape(', '.join(verdict['models_reported']) or '-')}</b> · peak GPU utilization: <b>{verdict['gpu_util_max'] if verdict['gpu_util_max'] is not None else 'n/a'} %</b></p>
<h2>GPU during the run (orange dashes = model calls from the trace)</h2>
{chart(series, 'util', 'GPU utilization', '%', t0, t1, calls, '#2dd4bf')}
{chart(series, 'power_w', 'GPU power draw', ' W', t0, t1, calls, '#60a5fa')}
{chart(series, 'mem_used_mib', 'GPU memory used', ' MiB', t0, t1, calls, '#a78bfa')}
<h2>Every connect() made by the GMAPS Python processes (exact, from the injected auditor)</h2><table><tr><th>t (s)</th><th>host</th><th>port</th><th>resolved</th><th>loopback</th></tr>{"".join(f"<tr><td>{c['ts'] - t0:.2f}</td><td>{html.escape(str(c['host']))}</td><td>{c['port']}</td><td>{html.escape(', '.join(c.get('resolved', [])))}</td><td>{c['local_only']}</td></tr>" for c in connects) or '<tr><td colspan="5">none</td></tr>'}</table>
<h2>TCP sockets seen on the GMAPS process tree (sampled from /proc)</h2><table><tr><th>remote</th><th>loopback</th><th>process</th><th>first seen (s)</th><th>states</th></tr>{rows(tree_conns.values(), ['remote', 'local_only', 'process', 'first_t', 'states'])}</table>
<h2>Every non-loopback ESTABLISHED connection on this machine during the run</h2><p class="na">Machine-wide, all processes (browser, SSH, Nightshift itself, etc.). Shown in full so a cloud call from any component would be visible.</p>
<table><tr><th>remote</th><th>local</th><th>pid</th><th>process</th><th>first seen (s)</th></tr>{rows(machine_remote.values(), ['remote', 'local', 'pid', 'process', 'first_t'])}</table>
<h2>Model calls (from runs/traces)</h2><table><tr><th>t (s)</th><th>role/step</th><th>provider</th><th>model_reported</th><th>latency ms</th><th>outcome</th></tr>{"".join(f"<tr><td>{c['t'] - t0:.1f}</td><td>{html.escape(c['role'] + '/' + c['step'])}</td><td>{html.escape(c['provider'])}</td><td>{html.escape(str(c['model_reported']))}</td><td>{c['latency_ms']}</td><td>{html.escape(c['outcome'])}</td></tr>" for c in calls) or '<tr><td colspan="6">none</td></tr>'}</table>
<p class="na">Generated by tools/local_proof.py from /proc and nvidia-smi samples every {a.interval} s. SIMULATED scenarios; decision support only.</p></body></html>'''
    (out / f'local_{ts}.html').write_text(page)
    print(json.dumps(verdict, indent=1))
    print(f'proof: runs/proof/local_{ts}.json  ·  runs/proof/local_{ts}.html')
    sys.exit(proc.returncode)


if __name__ == '__main__':
    main()
