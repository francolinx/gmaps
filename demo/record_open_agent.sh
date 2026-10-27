#!/usr/bin/env bash
# Open Agent Hackathon 2026: one command on the DGX Spark, with NIGHTSHIFT_BASE_URL / NIGHTSHIFT_API_KEY / NIGHTSHIFT_MODEL exported.
#  1. smoke test (runs/smoke/nightshift_<ts>.txt)
#  2. in the BACKGROUND: Nemotron vs the fine-tuned 4B on heldout4_asr (needs the NeMo Gym venv: GYMPY=<venv>/bin;
#     optional COMPARE_DEADLINE_UTC=HH:MM stops at a fixed prefix and records n)
#  3. the three scenarios live, recorded as the sample fixtures   4. replayed in sample mode with an empty environment
#  5. tests   6. wait for 2, then draw its chart
# Then review `git status` and commit runs/proof, runs/smoke, runs/tests, samples/open_agent, eval/results/open_agent_2026,
# docs/model_card/open_agent_2026, ui_scaffold/samples/open_agent_status.json.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${NIGHTSHIFT_API_KEY:-}" ] && [ -s "${NIGHTSHIFT_API_KEY_FILE:-$HOME/.nightshift_key}" ]; then
  export NIGHTSHIFT_API_KEY="$(cat "${NIGHTSHIFT_API_KEY_FILE:-$HOME/.nightshift_key}")"
fi
: "${NIGHTSHIFT_API_KEY:?set NIGHTSHIFT_API_KEY or write the runtime key to ~/.nightshift_key (chmod 600)}"
export NIGHTSHIFT_BASE_URL=${NIGHTSHIFT_BASE_URL:-http://127.0.0.1:8088/v1}
if [ -z "${NIGHTSHIFT_MODEL:-}" ]; then   # pick the first Nemotron model Nightshift lists (the NVIDIA award needs a Nemotron model)
  NIGHTSHIFT_MODEL=$(python3 tools/nightshift_smoke.py --models | grep -i nemotron | grep -v '^Nemotron ids' | head -1 || true)
  export NIGHTSHIFT_MODEL=${NIGHTSHIFT_MODEL:-meterless/auto}
fi
echo "Nightshift: $NIGHTSHIFT_BASE_URL  model: $NIGHTSHIFT_MODEL"
TS=$(date -u +%Y%m%d-%H%M%S)
GYMPY=${GYMPY:-$HOME/gymvenv313/bin}
mkdir -p runs/tests runs/smoke
python3 tools/nightshift_smoke.py
"$GYMPY/python" eval/run_provider_compare.py --set heldout4_asr --provider nightshift ${COMPARE_DEADLINE_UTC:+--deadline-utc $COMPARE_DEADLINE_UTC} \
  > "runs/tests/compare_heldout4_${TS}.txt" 2>&1 &
CMP=$!
# live scenarios under the local-execution proof: every connect() + GPU samples -> runs/proof/local_<ts>.{json,html}
python3 tools/local_proof.py --interval 0.5 -- demo/run_open_agent.py --scenario all --provider nightshift --record | tee "runs/tests/open_agent_live_${TS}.txt"
env -i PATH="$PATH" HOME="$HOME" python3 demo/run_open_agent.py --scenario all --provider sample | tee "runs/tests/open_agent_sample_replay_${TS}.txt"
python3 tests/run_all.py > "runs/tests/run_all_${TS}.txt" 2>&1 || true
tail -4 "runs/tests/run_all_${TS}.txt"
for f in samples/open_agent/traces/*.jsonl; do python3 tools/show_trace.py "$f" --calls; done | tee "runs/tests/open_agent_calls_${TS}.txt"
echo "waiting for the heldout4 comparison (pid $CMP; progress in runs/tests/compare_heldout4_${TS}.txt) ..."
wait $CMP && tail -40 "runs/tests/compare_heldout4_${TS}.txt"
"$GYMPY/python" tools/release_charts.py --out docs/model_card/open_agent_2026 \
  --open-agent-compare eval/results/open_agent_2026/nemotron_vs_parser_heldout4_asr.json
git grep -I -n -E 'sk-[A-Za-z0-9]{12,}|Bearer [A-Za-z0-9._-]{16,}' && echo 'SECRET SCAN: CHECK THE LINES ABOVE' || echo 'secret scan: clean'
