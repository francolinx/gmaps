"""Mock OpenAI-compatible chat server for proving the NeMo Gym orchestration WITHOUT a GPU.

It answers POST /v1/chat/completions like vLLM would, with a scripted policy looked up from the task files:
  gold     the task's gold JSON (expected reward 1.0)      grammar  the rule parser (gym/run_offline_policy.policy_grammar)
  empty    an empty string (expected reward 0.0)
The transmission is taken from the last user message ('Transmission: \"\"\"...\"\"\"'), so the mock knows nothing the policy
would not. This exists ONLY to exercise `gym eval run` end to end (servers, agent, verifier, rollouts, aggregate) and to pin
the model-type flags; its numbers never go in the scoreboard. SIMULATED data.

  python3 gym/mock_policy_server.py --policy gold --port 18001 &
  gym eval run ... --model-type vllm_model --model mock --model-url http://127.0.0.1:18001/v1 ...
"""
import argparse, json, re, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path[:0] = [str(HERE), str(REPO / 'lib'), str(REPO / 'venue')]
import run_offline_policy as OP   # noqa: E402

TASKS = {}


def load_tasks():
    for p in (HERE / 'environments/gmaps_parser/data').glob('*.jsonl'):
        for line in p.read_text().splitlines():
            if line.strip():
                t = json.loads(line)
                TASKS.setdefault(t['transmission'], t)


def transmission_of(messages):
    user = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
    if not user:
        return None
    c = user.get('content')
    if isinstance(c, list):
        c = ' '.join(x.get('text', '') for x in c if isinstance(x, dict))
    m = re.search(r'"""(.*?)"""', c or '', flags=re.S)
    return (m.group(1) if m else (c or '')).strip()


class Handler(BaseHTTPRequestHandler):
    policy = 'gold'
    model = 'mock'
    n = 0

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            return self._send(200, {'object': 'list', 'data': [{'id': self.model, 'object': 'model', 'created': 0, 'owned_by': 'mock'}]})
        if self.path.rstrip('/').endswith('/health'):
            return self._send(200, {'status': 'ok'})
        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        if not self.path.rstrip('/').endswith('/chat/completions'):
            return self._send(404, {'error': f'mock serves /v1/chat/completions only, got {self.path}'})
        tx = transmission_of(body.get('messages', []))
        task = TASKS.get(tx)
        if task is None:
            text = ''
        elif self.policy == 'gold':
            text = OP.policy_gold(task)
        elif self.policy == 'grammar':
            text = OP.policy_grammar(task)
        else:
            text = ''
        Handler.n += 1
        out = {'id': f'chatcmpl-mock-{Handler.n}', 'object': 'chat.completion', 'created': int(time.time()), 'model': body.get('model') or self.model,
               'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text, 'tool_calls': []}, 'finish_reason': 'stop', 'logprobs': None}],
               'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
        self._send(200, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--policy', choices=['gold', 'grammar', 'empty'], default='gold')
    ap.add_argument('--port', type=int, default=18001)
    ap.add_argument('--model', default='mock')
    a = ap.parse_args()
    load_tasks()
    Handler.policy, Handler.model = a.policy, a.model
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    print(f'mock policy {a.policy!r} as model {a.model!r} on http://127.0.0.1:{a.port}/v1 ({len(TASKS)} transmissions known)', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
