"""NightshiftLLM: OpenAI-compatible chat completions against Meterless Nightshift (Open Agent Hackathon 2026).

Nightshift runs on your own machine/network and exposes one OpenAI-compatible base URL and a runtime key (both shown in the
Nightshift Console); NVIDIA is added in Nightshift's Providers tab (the NVIDIA key lives there, never in this project).
NIGHTSHIFT_BASE_URL defaults to http://127.0.0.1:8088/v1 and may be pasted as host:port, .../v1, .../v1/models or
.../v1/chat/completions. NIGHTSHIFT_MODEL: a Nemotron id from `python3 tools/nightshift_smoke.py --models` (meterless/auto may
route to a non-NVIDIA model; model_reported in every trace line shows which model actually answered).

A NEW provider class: LocalLLM (gmaps_core.adapters) still refuses every non-local URL, and the Raleigh local path never
routes here. Credentials only from the environment (NIGHTSHIFT_API_KEY); nothing is written to disk except the trace,
which never contains the key. Temperature 0 for parsing. Timeout per call, one retry on HTTP 5xx / connection errors;
after that a typed 'provider_unavailable' result, never a guess. model_reported is read from the response body ('model').
"""
import json, os, time, urllib.error, urllib.request
from .base import ProviderBase

DEFAULT_MODEL = 'meterless/auto'          # set NIGHTSHIFT_MODEL to the Nemotron id Nightshift lists, for the NVIDIA award
DEFAULT_BASE = 'http://127.0.0.1:8088/v1'


def normalize_base(url):
    u = (url or DEFAULT_BASE).strip().rstrip('/')
    if '://' not in u:
        u = 'http://' + u
    for tail in ('/chat/completions', '/models', '/healthz'):
        if u.endswith(tail):
            u = u[: -len(tail)]
    return u if u.endswith('/v1') else u + '/v1'


class NightshiftLLM(ProviderBase):
    name = 'nightshift'

    def __init__(self, base_url=None, api_key=None, model=None, timeout=None, retries=1):
        self.base = normalize_base(base_url or os.environ.get('NIGHTSHIFT_BASE_URL'))
        self.key = api_key or os.environ.get('NIGHTSHIFT_API_KEY') or ''
        self.model = model or os.environ.get('NIGHTSHIFT_MODEL') or DEFAULT_MODEL
        self.timeout = float(timeout or os.environ.get('NIGHTSHIFT_TIMEOUT_S') or 30)
        self.retries = retries
        if not self.key:
            raise ValueError('NightshiftLLM needs NIGHTSHIFT_API_KEY (the runtime key from the Nightshift Console) in the environment')
        self.run_id = os.environ.get('GMAPS_RUN_ID', '')

    def headers(self):
        h = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key, 'meterless-app': 'gmaps', 'meterless-scope': 'user'}
        if self.run_id:
            h['X-Run-Id'] = self.run_id
        return h

    def models(self):
        """GET /v1/models -> list of model ids (raises on HTTP errors)."""
        req = urllib.request.Request(self.base + '/models', headers=self.headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return [m.get('id') for m in json.loads(r.read()).get('data', [])]

    def _post(self, body):
        req = urllib.request.Request(self.base + '/chat/completions', data=json.dumps(body).encode(), method='POST',
                                     headers=self.headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def chat(self, messages, **extra):
        body = {'model': self.model, 'messages': messages}
        body.update(extra)
        t0, attempts, last_err = time.perf_counter(), 0, None
        while attempts <= self.retries:
            attempts += 1
            try:
                d = self._post(body)
                break
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode(errors='replace')
                if e.code == 400 and 'response_format' in body:   # provider without structured output: same prompt, JSON checked after
                    body.pop('response_format'); attempts -= 1; last_err = f'HTTP 400 with response_format: {detail[:120]}'
                    continue
                last_err = f'HTTP {e.code}: {detail[:160]}'
                if e.code < 500:
                    break
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                last_err = f'{type(e).__name__}: {str(e)[:160]}'
        else:
            d = None
        lat = round((time.perf_counter() - t0) * 1000, 1)
        if last_err and not (isinstance(locals().get('d'), dict) and d.get('choices')):
            return self._result(messages, latency_ms=lat, error=f'provider_unavailable ({last_err})', attempts=attempts)
        try:
            content = d['choices'][0]['message'].get('content') or ''
        except (KeyError, IndexError, TypeError):
            return self._result(messages, latency_ms=lat, error='provider_unavailable (no choices in response)', attempts=attempts)
        u = d.get('usage') or {}
        return self._result(messages, ok=True, content=content, model_reported=d.get('model'), latency_ms=lat,
                            prompt_tokens=u.get('prompt_tokens'), completion_tokens=u.get('completion_tokens'), attempts=attempts)
