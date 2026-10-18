"""Persistent agent session for the GMAPS agent fallback (keep the session running between transmissions,
cap its context, fall back to a fresh session automatically if it crashes).

One long-lived client in the sandbox (venue/openclaw/gmaps_turns.mjs) pulls each
transmission from the host tool server's turn queue and runs it as a turn of the `gmaps` agent on the OpenClaw gateway.
No per-turn sandbox exec or CLI start. Context cap: the session key rotates every ROTATE_EVERY transmissions (a fresh
agent session; the ledger state lives in the tool server, not in the chat history). Crash handling: if the client process
is gone, stops polling, or a turn fails or times out, the client is restarted on a fresh session key and the
transmission is sent again once; if that also fails, the caller falls back to the per-turn CLI path (fresh session).
The client is started DETACHED inside the sandbox by one short exec (double fork): a long-running `nemoclaw exec` would
hold ~/.nemoclaw-portable-host.lock and make every other exec (the CLI fallback included) fail.
"""
import hashlib, json, subprocess, time, urllib.request, uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLIENT = REPO / 'venue/openclaw/gmaps_turns.mjs'
SANDBOX_CLIENT = '/sandbox/gmaps/gmaps_turns.mjs'
BASE = 'http://172.18.0.1:11435'
LOG = Path.home() / 'gmaps_venue' / 'log'
ROTATE_EVERY = 6


def _token():
    return (Path.home() / 'gmaps_venue/secrets/mcp_token').read_text().strip()


def _req(path, body=None, timeout=10):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Authorization': f'Bearer {_token()}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, (json.loads(resp.read() or b'null') if resp.status == 200 else None)


def sx(*cmd, stdin=None, timeout=60):
    return subprocess.run(['nemoclaw', 'my-assistant', 'exec', '--no-tty', *(['--stdin'] if stdin is not None else []), '--', *cmd],
                          input=stdin, capture_output=True, text=True, timeout=timeout)


def install_client():
    """Copy the client into the sandbox if its sha256 differs (operator action, not a model tool)."""
    want = hashlib.sha256(CLIENT.read_bytes()).hexdigest()
    have = sx('sh', '-c', f'sha256sum {SANDBOX_CLIENT} 2>/dev/null | cut -c1-64').stdout.strip()
    if have != want:
        sx('sh', '-c', f'mkdir -p /sandbox/gmaps && cat > {SANDBOX_CLIENT}', stdin=CLIENT.read_text())
    return want


class PersistentAgent:
    def __init__(self, run_id, rotate_every=ROTATE_EVERY, log_name=None):
        self.run_id, self.rotate_every = run_id, rotate_every
        self.gen, self.in_gen, self.restarts = 0, 0, 0
        self.client_log = LOG / (log_name or f'agent_client_{run_id}.log')

    @property
    def session_key(self):
        return f'agent:gmaps:{self.run_id}-s{self.gen}'

    def _status(self):
        try:
            return _req('/agent/status', timeout=3)[1] or {}
        except Exception:
            return {}

    def alive(self):
        age = self._status().get('client_seen_age_s')
        return age is not None and age < 30

    def client_running(self):
        return 'gmaps_turns.mjs' in sx('sh', '-c', 'pgrep -fa gmaps_turns.mjs | grep -v pgrep || true', timeout=30).stdout

    def start(self):
        self.stop()
        install_client()
        sx('sh', '-c', f'( setsid node {SANDBOX_CLIENT} gmaps >> /sandbox/gmaps/client.log 2>&1 < /dev/null & ) ; echo started', timeout=30)
        for _ in range(80):
            if self.alive():
                return True
            time.sleep(0.25)
        return False

    def stop(self):
        sx('sh', '-c', 'pkill -f gmaps_turns.mjs || true', timeout=30)   # no stale client may keep pulling turns
        t0 = time.time()
        while time.time() - t0 < 5 and self.client_running():
            time.sleep(0.5)

    def _fresh(self, why):
        self.gen += 1; self.in_gen = 0; self.restarts += 1
        with open(self.client_log, 'a') as f:
            f.write(json.dumps({'event': 'fresh_session', 'why': why, 'session_key': self.session_key, 't': time.time()}) + '\n')
        return self.start()

    def _one(self, message, timeout):
        rid = uuid.uuid4().hex
        _req('/agent/enqueue', {'id': rid, 'session_key': self.session_key, 'message': message, 'timeout_s': timeout})
        t0 = time.time(); end = t0 + timeout + 10; checked = t0
        if _req(f'/agent/ack?id={rid}&wait=3', timeout=10)[0] != 200 and not self.client_running():   # a live client acks within ms
            return {'ok': False, 'error': 'client process exited (message not acknowledged)'}
        while time.time() < end:
            st, res = _req(f'/agent/result?id={rid}&wait=4', timeout=15)
            if st == 200:
                return res
            if time.time() - checked >= 4:   # during the turn: is the client still there? (one short exec every ~5 s)
                checked = time.time()
                if not self.client_running():
                    return {'ok': False, 'error': 'client process exited (mid-turn)'}
        return {'ok': False, 'error': 'turn timed out'}

    def turn(self, message, timeout=120):
        """Returns (rc, content, wall_s, epoch_start, info). rc != 0 means the caller must use the per-turn CLI fallback."""
        e0, t0, info = time.time(), time.perf_counter(), {'restarts_before': self.restarts}
        if self.in_gen >= self.rotate_every:          # context cap: a fresh session every rotate_every transmissions
            self.gen += 1; self.in_gen = 0; info['rotated'] = True
        if not self.alive() and not self._fresh('client not running'):
            return 1, None, round(time.perf_counter() - t0, 2), e0, dict(info, error='client did not start')
        res = self._one(message, timeout)
        if not res.get('ok'):                         # crash or failed turn: fresh session, send once more
            info['first_error'] = res.get('error') or res.get('status')
            if self._fresh(f"turn failed: {info['first_error']}"):
                res = self._one(message, timeout)
        self.in_gen += 1
        info.update(session_key=self.session_key, client_ms=res.get('ms'), restarts=self.restarts - info['restarts_before'])
        return (0 if res.get('ok') else 1), res.get('content'), round(time.perf_counter() - t0, 2), e0, dict(info, error=None if res.get('ok') else res.get('error'))
