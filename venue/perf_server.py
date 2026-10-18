"""GMAPS live GB10 telemetry (venue code). Stdlib only. Real data only; nothing simulated.

  python3 venue/perf_server.py &      ->  http://localhost:8766

Samples every 1 s, keeps 10 min in memory: nvidia-smi (util, power, temp, SM clock; GB10 memory fields are N/A and
are not shown), unified memory (/proc/meminfo), CPU % (/proc/stat), vLLM Prometheus metrics + /health, docker ps,
port checks (gmaps tool server 172.18.0.1:11435, OpenShell gateway :8080), latest demo run from ~/gmaps_venue/log.
"""
import collections, glob, json, os, re, socket, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = Path.home() / 'gmaps_venue' / 'log'
VLLM = 'http://127.0.0.1:8000'
HIST = collections.deque(maxlen=600)
STATE = {'status': {}, 'run': {}}
LOCK = threading.Lock()


def sh(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ''


def gpu():
    out = sh(['nvidia-smi', '--query-gpu=utilization.gpu,power.draw,temperature.gpu,clocks.sm', '--format=csv,noheader,nounits'])
    v = [x.strip() for x in out.strip().split(',')] if out.strip() else []
    num = lambda s: float(s) if re.fullmatch(r'[0-9.]+', s or '') else None
    return dict(zip(('gpu_util', 'power_w', 'temp_c', 'sm_mhz'), [num(x) for x in v])) if len(v) == 4 else {}


def mem():
    m = {}
    for ln in open('/proc/meminfo'):
        k, v = ln.split(':', 1)
        m[k] = int(v.split()[0])
    tot, avail = m['MemTotal'] / 1048576, m['MemAvailable'] / 1048576
    return {'mem_used_gib': round(tot - avail, 2), 'mem_total_gib': round(tot, 2)}


_cpu_prev = None


def cpu():
    global _cpu_prev
    f = [int(x) for x in open('/proc/stat').readline().split()[1:]]
    idle, total = f[3] + f[4], sum(f)
    pct = None
    if _cpu_prev:
        dt, di = total - _cpu_prev[1], idle - _cpu_prev[0]
        pct = round(100 * (1 - di / dt), 1) if dt else None
    _cpu_prev = (idle, total)
    return {'cpu_pct': pct}


_tok_prev = None


def vllm():
    global _tok_prev
    out = {'vllm_health': None}
    try:
        with urllib.request.urlopen(VLLM + '/health', timeout=2) as r:
            out['vllm_health'] = r.status
    except Exception:
        out['vllm_health'] = 0
    try:
        with urllib.request.urlopen(VLLM + '/metrics', timeout=2) as r:
            txt = r.read().decode()
    except Exception:
        return out
    g = lambda name: sum(float(x) for x in re.findall(rf'^vllm:{name}{{[^}}]*}} ([0-9.e+]+)$', txt, re.M)) if re.search(rf'^vllm:{name}{{', txt, re.M) else None
    out.update(req_running=g('num_requests_running'), req_waiting=g('num_requests_waiting'), kv_cache_pct=g('kv_cache_usage_perc'))
    if out['kv_cache_pct'] is not None:
        out['kv_cache_pct'] = round(100 * out['kv_cache_pct'], 2)
    now, pt, gt = time.time(), g('prompt_tokens_total'), g('generation_tokens_total')
    if _tok_prev and pt is not None and gt is not None:
        dt = now - _tok_prev[0]
        out['prompt_tok_s'] = round((pt - _tok_prev[1]) / dt, 1)
        out['gen_tok_s'] = round((gt - _tok_prev[2]) / dt, 1)
    _tok_prev = (now, pt, gt)
    return out


def port_up(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def slow_status():
    ps = sh(['docker', 'ps', '--no-trunc', '--format', '{{.Names}}|{{.Status}}|{{.Command}}'])
    containers = [dict(zip(('name', 'status', 'command'), l.split('|', 2))) for l in ps.strip().splitlines() if l]
    vllm_c = next((c for c in containers if c['name'] == 'nemoclaw-vllm'), None)
    whisper = [c for c in containers if 'asr_clips' in c['command'] or 'asr_real' in c['command']]
    run = {}
    turns = sorted(glob.glob(str(LOG / 'agent_turns_*.jsonl')), key=os.path.getmtime)
    if turns:
        f = turns[-1]
        rid = Path(f).stem.replace('agent_turns_', '')
        rows = [json.loads(l) for l in open(f) if l.strip()]
        run = {'run_id': rid, 'updated': time.strftime('%H:%M:%S', time.localtime(os.path.getmtime(f))),
               'turns': [{'t': r['t'], 'wall_s': r['wall_s'], 'asr_s': r.get('asr_s'), 'input': r.get('input_source')} for r in rows]}
        a = LOG / f'asr_{rid}' / 'asr_results.json'
        if a.exists():
            run['asr'] = json.loads(a.read_text())['summary']
    last_alert = None
    for f in sorted(glob.glob(str(LOG / 'mcp_calls_*.jsonl')), key=os.path.getmtime)[-12:]:
        for l in open(f):
            if '"post_alert"' in l:
                r = json.loads(l)
                if r.get('ok') and (r.get('result') or {}).get('status') == 'sent':
                    last_alert = {'message_id': r['result']['message_id'], 'tier': r['result'].get('tier'), 'at': r['iso'], 'run_id': r['run_id']}
    return {'vllm_container': vllm_c and vllm_c['status'], 'whisper_containers': [c['status'] for c in whisper],
            'gmaps_tools_up': port_up('172.18.0.1', 11435), 'openshell_8080_up': port_up('127.0.0.1', 8080),
            'last_alert': last_alert}, run


def sampler():
    i = 0
    while True:
        t0 = time.time()
        s = {'ts': round(t0, 1)}
        for fn in (gpu, mem, cpu, vllm):
            try:
                s.update(fn())
            except Exception as e:
                s[fn.__name__ + '_error'] = str(e)[:80]
        with LOCK:
            HIST.append(s)
        if i % 5 == 0:
            try:
                st, run = slow_status()
                with LOCK:
                    STATE['status'], STATE['run'] = st, run
            except Exception as e:
                STATE['status'] = {'error': str(e)[:120]}
        i += 1
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith('/data'):
            with LOCK:
                body = json.dumps({'history': list(HIST), 'status': STATE['status'], 'run': STATE['run'], 'now': time.time()}).encode()
            ctype = 'application/json'
        elif self.path in ('/', '/index.html'):
            body, ctype = (HERE / 'perf.html').read_bytes(), 'text/html; charset=utf-8'
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(body)


if __name__ == '__main__':
    threading.Thread(target=sampler, daemon=True).start()
    print('GMAPS perf telemetry on http://localhost:8766', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8766), H).serve_forever()
