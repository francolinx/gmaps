"""Public-snapshot runner for tests/run_all_checks.py (the full check list, unchanged).

usage: python3 tests/run_all.py
Each check of run_all_checks.py (split at its '# ---' headers and after every check() statement) runs in one shared
namespace, in order. A check that needs a file left out of the public
snapshot (a historical fixture, private review audio and text, git hooks, systemd units, internal reports) is
SKIPPED with that reason; it is never counted as passed. Exit code 0 = no check failed.
"""
import ast, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / 'run_all_checks.py').read_text()
LINES = SRC.splitlines(keepends=True)
TREE = ast.parse(SRC)
HEADERS = [i + 1 for i, l in enumerate(LINES) if re.match(r'#\s*-{2,}', l)]
END = next(s.lineno for s in TREE.body if isinstance(s, ast.Assign) and ast.unparse(s).startswith('ok = all('))   # the summary
SKIP_REASON = 'not in the public snapshot'


def section_of(lineno):
    return max([0] + [h for h in HEADERS if h <= lineno])


def check_names(stmts):
    """The check(...) calls written in these statements, by their first argument (static; a loop counts once per call site)."""
    out = []
    for s in stmts:
        for n in ast.walk(s):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'check' and n.args:
                a = n.args[0]
                out.append(a.value if isinstance(a, ast.Constant) else ast.unparse(a)[:120])
    return out


sections, cur = {}, None   # a new section starts at each '# ---' header and after each statement that calls check()
for s in TREE.body:
    if s.lineno >= END:
        break
    if cur is None or section_of(s.lineno) != section_of(sections[cur][-1].lineno):
        cur = s.lineno
    sections.setdefault(cur, []).append(s)
    if check_names([s]):
        cur = None


import hashlib, json, os, subprocess
SKIPS_FILE = HERE / 'public_snapshot_skips.json'   # sha256(path) -> reason, for paths the checks name that this copy does not have
SKIPS = json.loads(SKIPS_FILE.read_text()) if SKIPS_FILE.exists() else {}
RECORD = os.environ.get('GMAPS_RECORD_SKIPS')        # path of the full repository: rebuild SKIPS_FILE by comparing with it


def _h(part):
    return hashlib.sha256(part.encode()).hexdigest()


def reason_for(part):
    """Why this path is absent: generated airport build output (gitignored) or a file left out of the public snapshot."""
    if '/build/' in part or part.endswith('/build'):
        icao = part.split('/')[2].upper() if part.startswith('data/airports/') and part.count('/') >= 2 else '<ICAO>'
        return f'{SKIP_REASON} (generated, not committed: python3 tools/build_airport.py {icao})'
    return SKIP_REASON


def validate_sources_missing():
    """validate.py --self-test fails only because the airport source notes cited in the provenance were left out."""
    r = subprocess.run([sys.executable, 'validate.py', '--self-test'], cwd=HERE.parent / 'data/airports', capture_output=True, text=True)
    errs = [e for a in json.loads(r.stdout).get('airports', []) for e in a.get('errors', [])]
    return f'{SKIP_REASON}: {len(set(errs))} airport source notes cited in the provenance' if errs and all(e.startswith('missing source file:') for e in errs) else None


def parser_files_short():
    """The parser-comparison check wants three files; one of them is the left-out fixture's. The files present must still pass."""
    return f'{SKIP_REASON}: one of the three parser-comparison files' if not G.get('_pc_bad') and len(list((HERE.parent / 'ui_scaffold/samples').glob('parsers_*.json'))) == 2 else None


def regress_needs_builds():
    """regress.py compares rebuilt outputs; without the generated build/ folders it cannot run."""
    missing = sorted(p.name for p in (HERE.parent / 'data/airports').iterdir() if (p / 'review/regress_baseline.json').exists() and not (p / 'build').exists())
    return f'{SKIP_REASON} (generated, not committed: python3 tools/build_airport.py <ICAO>, {len(missing)} airports)' if missing else None


VIA_SUBPROCESS = {'graphs: validate.py --self-test': validate_sources_missing, 'parser-comparison files:': parser_files_short, 'regress.py:': regress_needs_builds}


def left_out(stmts, name):
    """Reasons for the paths this section names (string literals with a '/', globs included) that this copy does not have."""
    for k, fn in VIA_SUBPROCESS.items():
        if name.startswith(k):
            r = fn()
            return [r] if r else []
    lits = {n.value for s in stmts for n in ast.walk(s) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    lits |= {m[len(str(HERE.parent)) + 1:] for m in re.findall(r"No such file or directory: '([^']+)'", details.get(name, '')) if m.startswith(str(HERE.parent) + '/')}
    for n in {n.id for s in stmts for n in ast.walk(s) if isinstance(n, ast.Name)}:   # Path variables set in earlier sections
        v = G.get(n)
        if isinstance(v, Path) and str(v).startswith(str(HERE.parent) + '/'):
            lits.add(str(v.relative_to(HERE.parent)))
    out = []
    for x in sorted(lits):
        for part in re.findall(r'[\w.*-]+(?:/[\w.*-]+)+', x):
            part = part.strip('/')
            if RECORD:
                full = Path(RECORD)
                n_here = len(list(HERE.parent.glob(part))) if '*' in part else int((HERE.parent / part).exists())
                n_there = len(list(full.glob(part))) if '*' in part else int((full / part).exists())
                if n_there > n_here:
                    SKIPS[_h(part)] = reason_for(part)
            if _h(part) in SKIPS and SKIPS[_h(part)] not in out:
                out.append(SKIPS[_h(part)])
    return out


G = {'__file__': str(HERE / 'run_all.py'), '__name__': '__main__'}
skipped = []   # (check name, reason)
skipped_keys = {}
details = {}
for key in sorted(sections):
    stmts = sections[key]
    a, b = stmts[0].lineno, stmts[-1].end_lineno
    code = '\n' * (a - 1) + ''.join(LINES[a - 1:b])
    before = len(G.get('results', []))
    try:
        exec(compile(code, str(HERE / 'run_all_checks.py'), 'exec'), G)
    except FileNotFoundError as ex:
        done = len(G.get('results', [])) - before
        miss = Path(ex.filename or '?')
        try:
            miss = miss.relative_to(HERE.parent)
        except ValueError:
            pass
        names = check_names(stmts)[done:] or [f'section at line {key}']
        why = reason_for(str(miss))
        if RECORD:
            SKIPS[_h(str(miss))] = why
        skipped += [(n, why) for n in names]
        skipped_keys[key] = str(miss)
    except NameError as ex:   # uses a name defined only in a section that was skipped above
        if not skipped_keys:
            raise
        done = len(G.get('results', [])) - before
        names = check_names(stmts)[done:] or [f'section at line {key}']
        skipped += [(n, f'{SKIP_REASON} (needs a check skipped above)') for n in names]
        skipped_keys[key] = ex.name

        continue
    else:
        for i in range(before, len(G['results'])):   # a failed check whose files were left out is a skip, not a pass or a fail
            n, ok_, d = G['results'][i]
            details[n] = d
            gone = [] if ok_ else left_out(stmts, n)
            if gone:
                G['results'][i] = None
                skipped.append((n, '; '.join(gone)))
        G['results'][:] = [r for r in G['results'] if r is not None]

if RECORD:
    SKIPS_FILE.write_text(json.dumps(dict(sorted(SKIPS.items())), indent=1) + '\n')
results = G['results']
for n, passed, d in results:
    print(('PASS ' if passed else 'FAIL ') + n + (f'  [{d}]' if d else ''))
for n, why in skipped:
    print(f'SKIP {n}  [{why}]')
fails = sum(not r[1] for r in results)
print(f"\n{sum(r[1] for r in results)} passed, {len(skipped)} skipped (public snapshot)" + (f', {fails} FAILED' if fails else ''))

# Open Agent Hackathon 2026 (new work, branch open-agent-2026): unittest modules, counted separately from the snapshot checks above.
import unittest
sys.path.insert(0, str(HERE))
_suite = unittest.defaultTestLoader.loadTestsFromNames(['test_open_agent', 'test_meterless_engine', 'test_zetaris_store', 'test_lga_scene'])
sys.path.insert(0, str(HERE))
_r = unittest.TextTestRunner(stream=open(os.devnull, 'w'), verbosity=0).run(_suite)
for _t, _why in _r.skipped:
    print(f'SKIP open-agent {_t.id()}  [{_why}]')
for _t, _tb in _r.failures + _r.errors:
    print(f'FAIL open-agent {_t.id()}\n{_tb}')
if not (os.environ.get('NIGHTSHIFT_API_KEY') and os.environ.get('NIGHTSHIFT_BASE_URL')):
    print('SKIP open-agent live Nightshift smoke (tools/nightshift_smoke.py)  [NIGHTSHIFT_API_KEY / NIGHTSHIFT_BASE_URL not set]')
_oa_fail = len(_r.failures) + len(_r.errors)
print(f"open-agent: {_r.testsRun - len(_r.skipped) - _oa_fail} passed, {len(_r.skipped)} skipped" + (f', {_oa_fail} FAILED' if _oa_fail else ''))
sys.exit(0 if not fails and not _oa_fail else 1)
