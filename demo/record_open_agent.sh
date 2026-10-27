#!/usr/bin/env bash
# Open Agent Hackathon 2026: one command on the DGX Spark, with NIGHTSHIFT_BASE_URL / NIGHTSHIFT_API_KEY / NIGHTSHIFT_MODEL exported.
#  1. smoke test (runs/smoke/nightshift_<ts>.txt)   2. the three scenarios live, recorded as the sample fixtures
#  3. the same three replayed in sample mode with an empty environment (no key, no network)   4. tests
# Then review `git status` and commit runs/smoke, runs/tests, samples/open_agent, ui_scaffold/samples/open_agent_status.json.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${NIGHTSHIFT_BASE_URL:?export NIGHTSHIFT_BASE_URL}" "${NIGHTSHIFT_API_KEY:?export NIGHTSHIFT_API_KEY}"
TS=$(date -u +%Y%m%d-%H%M%S)
mkdir -p runs/tests runs/smoke
python3 tools/nightshift_smoke.py
python3 demo/run_open_agent.py --scenario all --provider nightshift --record | tee "runs/tests/open_agent_live_${TS}.txt"
env -i PATH="$PATH" HOME="$HOME" python3 demo/run_open_agent.py --scenario all --provider sample | tee "runs/tests/open_agent_sample_replay_${TS}.txt"
python3 tests/run_all.py > "runs/tests/run_all_${TS}.txt" 2>&1 || true
tail -4 "runs/tests/run_all_${TS}.txt"
for f in samples/open_agent/traces/*.jsonl; do python3 tools/show_trace.py "$f" --calls; done | tee "runs/tests/open_agent_calls_${TS}.txt"
git grep -I -n -E 'sk-[A-Za-z0-9]{12,}|Bearer [A-Za-z0-9._-]{16,}' && echo 'SECRET SCAN: CHECK THE LINES ABOVE' || echo 'secret scan: clean'
