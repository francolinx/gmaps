"""TraceLogger (Open Agent Hackathon 2026): one JSON line per model call and per agent step.

File: runs/traces/<run_id>.jsonl. Every line names the step, the role (planner / executor / evaluator / risk / explainer /
alert / engine / store), the provider, the model requested and the model the provider reported in its response body, latency,
token counts, the outcome (accepted / rejected / retried / escalated / unavailable / ok) and the grounding verdict.
A model-call line also carries request_sha (sha256 of the exact request messages) and the response text, so a recorded run
can be replayed offline by the sample provider (providers/sample.py). Read with: python3 tools/show_trace.py <file>
"""
import hashlib, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIELDS = ('ts', 'run_id', 'scenario', 'step', 'role', 'provider', 'model_requested', 'model_reported', 'latency_ms',
          'prompt_tokens', 'completion_tokens', 'outcome', 'grounding')


def request_sha(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class TraceLogger:
    def __init__(self, run_id, scenario=None, out_dir=None):
        self.run_id, self.scenario = run_id, scenario
        self.path = Path(out_dir or ROOT / 'runs' / 'traces') / f'{run_id}.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.n = 0

    def log(self, step, role, outcome='ok', provider=None, model_requested=None, model_reported=None, latency_ms=None,
            prompt_tokens=None, completion_tokens=None, grounding=None, **extra):
        rec = {'ts': round(time.time(), 3), 'run_id': self.run_id, 'scenario': self.scenario, 'seq': self.n, 'step': step, 'role': role,
               'provider': provider, 'model_requested': model_requested, 'model_reported': model_reported, 'latency_ms': latency_ms,
               'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens, 'outcome': outcome, 'grounding': grounding}
        rec.update(extra)
        with self.path.open('a') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self.n += 1
        return rec

    def log_call(self, step, role, result, outcome, grounding=None, **extra):
        """result: a providers.* ChatResult dict."""
        return self.log(step, role, outcome, provider=result.get('provider'), model_requested=result.get('model_requested'),
                        model_reported=result.get('model_reported'), latency_ms=result.get('latency_ms'),
                        prompt_tokens=result.get('prompt_tokens'), completion_tokens=result.get('completion_tokens'),
                        grounding=grounding, request_sha=result.get('request_sha'), response=result.get('content'),
                        error=result.get('error'), replayed_from=result.get('replayed_from'), **extra)


def read(path):
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
