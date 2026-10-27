"""Provider selection (Open Agent Hackathon 2026): GMAPS_PROVIDER=nightshift|local|sample.

Default: nightshift when NIGHTSHIFT_API_KEY and NIGHTSHIFT_BASE_URL are set, else sample (one-line banner on stderr).
'local' wraps the pre-existing LocalLLM (refuses non-local URLs; GMAPS_LLM_BASE_URL / GMAPS_LLM_MODEL).
"""
import os, sys
from .base import ProviderBase, Unavailable


def _local():
    from ..adapters import LocalLLM
    from .base import ProviderBase as _B
    import time

    class LocalProvider(_B):
        name = 'local'

        def __init__(self):
            self.model = os.environ.get('GMAPS_LLM_MODEL', 'gmaps-parser')
            self.llm = LocalLLM(os.environ.get('GMAPS_LLM_BASE_URL', 'http://127.0.0.1:8001/v1'), self.model)

        def chat(self, messages, **extra):
            t0 = time.perf_counter()
            try:
                d = self.llm.chat(messages, **extra)
            except Exception as e:
                return self._result(messages, error=f'provider_unavailable ({type(e).__name__})', latency_ms=round((time.perf_counter() - t0) * 1000, 1))
            u = d.get('usage') or {}
            return self._result(messages, ok=True, content=d['choices'][0]['message'].get('content') or '', model_reported=d.get('model'),
                                latency_ms=round((time.perf_counter() - t0) * 1000, 1), prompt_tokens=u.get('prompt_tokens'),
                                completion_tokens=u.get('completion_tokens'))
    return LocalProvider()


def get_provider(name=None, quiet=False):
    name = name or os.environ.get('GMAPS_PROVIDER')
    if not name:
        name = 'nightshift' if os.environ.get('NIGHTSHIFT_API_KEY') and os.environ.get('NIGHTSHIFT_BASE_URL') else 'sample'
        if name == 'sample' and not quiet:
            print('[gmaps] no NIGHTSHIFT_API_KEY / NIGHTSHIFT_BASE_URL: SAMPLE MODE, replaying recorded Nightshift traces (no network)', file=sys.stderr)
    if name == 'nightshift':
        from .nightshift import NightshiftLLM
        return NightshiftLLM()
    if name == 'sample':
        from .sample import SampleLLM
        return SampleLLM()
    if name == 'local':
        return _local()
    raise ValueError(f'unknown provider {name!r} (nightshift | local | sample)')
