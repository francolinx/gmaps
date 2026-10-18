"""CPU check of venue/tool_loop_bench.py: the bench must separate a model that follows the procedure from one that never
retries a failed call and one that hammers an unavailable look-ahead. No model, no GPU; results go to a temp dir.

  python3 tests/test_tool_loop_bench.py
"""
import contextlib, io, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / 'venue'), str(REPO / 'lib')]
import tool_loop_bench as B   # noqa: E402


def bench(mode):
    out = tempfile.mkdtemp(prefix=f'toolbench_test_{mode}_')
    with contextlib.redirect_stdout(io.StringIO()):
        return B.main(['--mock', mode, '--repeats', '1', '--out', out])['summary']


def main():
    good, naive, stubborn, good3 = bench('good'), bench('naive'), bench('stubborn'), bench('good3')
    checks = {
        'good passes every scenario': all(s['pass_rate'] == 1.0 for s in good.values()),
        'good recovers from the injected transient fault': good['transient']['recoveries'] >= 1 and good['transient']['faults_injected'] == 1,
        'good posts the E1 conflict once (time to alert measured)': good['positive']['alerts_exact_rate'] == 1.0 and good['positive']['time_to_alert_median_s'] is not None,
        'naive fails transient (no retry -> event never ingested, no alert)': naive['transient']['pass_rate'] == 0.0 and naive['transient']['ingest_recall_mean'] < 1.0,
        'naive fails positive (re-posts the same warning; loop refuses)': naive['positive']['pass_rate'] == 0.0 and naive['positive']['refused_reposts'] >= 1,
        'stubborn fails no_feed (refused retries counted against it)': stubborn['no_feed']['pass_rate'] == 0.0 and stubborn['no_feed']['refused_retries'] >= 1,
        'stubborn fails stale (injected unavailable look-ahead, then a retry)': stubborn['stale']['pass_rate'] == 0.0,
        'stubborn still passes positive (the bench is not just "always fail")': stubborn['positive']['pass_rate'] == 1.0,
        'good3 (alert turn ends at post_alert, its lookahead_used counts) passes every scenario': all(s['pass_rate'] == 1.0 for s in good3.values()),
    }
    ok = True
    for k, v in checks.items():
        print(('PASS ' if v else 'FAIL ') + k); ok &= bool(v)
    print(f'{sum(checks.values())}/{len(checks)} passed')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
