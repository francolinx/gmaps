"""pytest view of tests/run_all.py (cloud kit): one item per "# ---" section, run in file order in one shared
namespace, so every check keeps its exact code. A section passes when every check() it records passes.

Sections that need the Spark host itself carry the marker "spark" (registered in pytest.ini). On a CPU-only box:

  CUDA_VISIBLE_DEVICES="" python -m pytest -m "not spark"

python3 tests/run_all.py stays the full gate (the pre-commit hook runs it). No test logic lives here.
"""
import re
from pathlib import Path

import pytest

RUN_ALL = Path(__file__).resolve().with_name('run_all.py')

# Section headers (substring of the "# ---" line) whose checks need the Spark host: real clip sets / labels under
# ~/gmaps_venue, and this clone's installed pre-push hook. Nothing else in run_all.py needs GPU, models or running services:
# the server/audio/display checks start their own loopback servers on port 0 and the sandbox checks use fake binaries.
SPARK = ('real-clip readiness', 'N5 (Franco, Oct 10)')


def _sections():
    lines = RUN_ALL.read_text().splitlines(keepends=True)
    end = next(i for i, ln in enumerate(lines) if ln.startswith('ok = all('))   # the summary/exit block is not run here
    starts = [i for i, ln in enumerate(lines[:end]) if ln.startswith('# ---')]
    out = [('setup', ''.join(lines[:starts[0]]), starts[0])] if starts else []
    for a, b in zip(starts, starts[1:] + [end]):
        title = re.sub(r'^#\s*-+\s*|\s*-+\s*$', '', lines[a]).strip()
        out.append((title, '\n' * a + ''.join(lines[a:b]), a + 1))   # leading newlines keep tracebacks on run_all.py line numbers
    return out


SECTIONS = _sections()
NS = {'__file__': str(RUN_ALL), '__name__': 'run_all_sections'}


def _id(title, line):
    return f'L{line:04d}-' + (re.sub(r'[^A-Za-z0-9]+', '_', title).strip('_')[:60] or 'section')


@pytest.mark.parametrize('title,code,line', [
    pytest.param(t, c, ln, id=_id(t, ln), marks=[pytest.mark.spark] if any(k in t for k in SPARK) else [])
    for t, c, ln in SECTIONS])
def test_section(title, code, line):
    n0 = len(NS.get('results', []))
    exec(compile(code, str(RUN_ALL), 'exec'), NS)
    failed = [(n, d) for n, ok, d in NS['results'][n0:] if not ok]
    assert not failed, '\n'.join(f'FAIL {n}  [{d}]' for n, d in failed)
