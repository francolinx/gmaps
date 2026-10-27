"""SampleLLM: replays model answers recorded in real Nightshift runs (samples/open_agent/traces/*.jsonl), no network.

A request is matched by request_sha (sha256 of the exact messages); the agent loop is deterministic, so the same scenario
produces the same requests. A request that was never recorded returns 'provider_unavailable (not recorded)': the loop then
takes its fallback paths, exactly as it would if the provider were down. Replayed lines carry replayed_from and the recorded
model_reported; latency_ms is left empty (a replay is not a measurement; recorded_latency_ms is kept for reference).
"""
import json
from pathlib import Path
from .base import ProviderBase

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / 'samples' / 'open_agent' / 'traces'


class SampleLLM(ProviderBase):
    name = 'sample'

    def __init__(self, traces_dir=None):
        self.dir = Path(traces_dir or SAMPLES)
        self.index = {}
        for p in sorted(self.dir.glob('*.jsonl')) if self.dir.exists() else []:
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get('request_sha') and r.get('response') is not None and r.get('provider') == 'nightshift' and not r.get('error'):
                    self.index[r['request_sha']] = dict(r, _file=str(p.relative_to(ROOT)) if p.resolve().is_relative_to(ROOT) else str(p))
        self.model = 'replay'

    def chat(self, messages, **extra):
        res = self._result(messages)
        rec = self.index.get(res['request_sha'])
        if rec is None:
            res['error'] = 'provider_unavailable (not recorded: no sample trace for this request)'
            return res
        res.update(ok=True, content=rec['response'], model_requested=rec.get('model_requested'), model_reported=rec.get('model_reported'),
                   prompt_tokens=rec.get('prompt_tokens'), completion_tokens=rec.get('completion_tokens'),
                   replayed_from=rec['_file'], recorded_latency_ms=rec.get('latency_ms'))
        return res
