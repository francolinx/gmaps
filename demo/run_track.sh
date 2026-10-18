#!/usr/bin/env bash
# One 60-second live demo per track against the running parser server (addendum C.1). Clean output, no side effects
# beyond the trace/feed files the tools write under ~/gmaps_venue/demo_runs/.
#   demo/run_track.sh finetune | tools | gym | vllm | fastpath | sim
# Env: PORT (default: ~/gmaps_venue/log/phase3_port, else 8001), TEXT (finetune: the garbled transmission), AIRPORT (default KLGA),
#      MODEL and PROCEDURE (tools only; default model base, the loop's default procedure), e.g. PORT=8002 MODEL=lightning PROCEDURE=v3.
# Expected outputs: docs/demo_captures/<track>.txt. Runbook: docs/DEMO_RUNBOOK_RALEIGH.md.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"; cd "$REPO"
TRACK="${1:-}"
PORT="${PORT:-$(cat "$HOME/gmaps_venue/log/phase3_port" 2>/dev/null || echo 8001)}"
URL="http://127.0.0.1:$PORT/v1"; AIRPORT="${AIRPORT:-KLGA}"
GYMPY="${GYMPY:-$HOME/gymvenv313/bin}"
OUTD="$HOME/gmaps_venue/demo_runs"; mkdir -p "$OUTD"
hr() { printf '\n--- %s\n' "$*"; }
up() { curl -sf --max-time 3 "$URL/models" > /dev/null 2>&1; }
need_server() { up || { echo "parser server not answering on $URL: run venue/start_demo.sh, or show docs/demo_captures/$TRACK.txt"; exit 3; }; }

case "$TRACK" in
  finetune)
    need_server
    # default: a Whisper-garbled held-out transmission where the tuned adapter and the base model differ (picked from tonight's rollouts)
    TEXT="${TEXT:-$(cat "$REPO/demo/finetune_text.txt" 2>/dev/null || echo 'delta five sixteen runway four cleared for take off')}"
    hr "transmission (SIMULATED, Whisper-style noise): $TEXT"
    for M in base gmaps-parser; do
      hr "model: $M"
      python3 venue/llm_fallback.py --airport "$AIRPORT" --base-url "$URL" --model "$M" --text "$TEXT" 2>&1 | tail -15
    done
    hr "held-out scoreboard (docs/SCOREBOARD.md)"
    sed -n '1,40p' docs/SCOREBOARD.md 2>/dev/null | grep -E '^\||^#' | head -30
    ;;
  tools)
    need_server
    TMODEL="${MODEL:-base}"
    hr "tool loop: SIM_E1_CROSS_DURING_ROLLOUT (fictional SIM-1 map), model $TMODEL${PROCEDURE:+, procedure $PROCEDURE}, bounded tools only"
    python3 venue/tool_loop.py --episode SIM_E1_CROSS_DURING_ROLLOUT --base-url "$URL" --model "$TMODEL" ${PROCEDURE:+--procedure "$PROCEDURE"} --emit-feed \
      --trace-dir "$OUTD/traces" --feed-root "$OUTD" 2>&1 | tail -60
    hr "trace files"; ls -t "$OUTD/traces" 2>/dev/null | head -3 | sed "s|^|$OUTD/traces/|"
    ;;
  gym)
    hr "NeMo Gym rollouts on the held-out validation sets (orchestrated, verifier = record exact match)"
    ls -la gym/rollouts/validation_*.jsonl 2>/dev/null | awk '{print $5, $9}'
    for f in gym/rollouts/validation_gmaps-parser.jsonl gym/rollouts/validation_base.jsonl; do
      [ -f "$f" ] || continue; hr "by set: $f"; "$GYMPY/python" gym/aggregate_by_set.py "$f" 2>&1 | tail -15
    done
    ;;
  vllm)
    need_server
    hr "vLLM server on $URL"
    curl -s "$URL/models" | python3 -c "import json,sys; [print(' ', m['id'], '<-', m.get('root') or m.get('parent') or '') for m in json.load(sys.stdin)['data']]"
    hr "serve command (one 4B base + the LoRA adapter, 0.15 of GPU memory)"
    grep -m1 -A4 'docker run' venue/start_demo.sh | sed 's/^/  /'
    hr "latency (one request each)"
    for M in base gmaps-parser; do
      python3 - "$URL" "$M" <<'EOF'
import json, sys, time, urllib.request
url, m = sys.argv[1], sys.argv[2]
body = json.dumps({'model': m, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}], 'max_tokens': 8, 'temperature': 0}).encode()
t = time.time(); r = json.load(urllib.request.urlopen(urllib.request.Request(url + '/chat/completions', body, {'Content-Type': 'application/json'}), timeout=60))
print(f'  {m:13s} {1000 * (time.time() - t):6.0f} ms  completion_tokens={r["usage"]["completion_tokens"]}')
EOF
    done
    ;;
  fastpath)
    hr "fast path (grammar first, LLM fallback only on residue), TEXT mode, one scenario"
    python3 -m sim.run_all --text --only Q5_01_LAND_VS_CROSS --no-docs --out-json "$OUTD/fastpath.json" ${URL:+--llm-url "$URL" --llm-model gmaps-parser} 2>&1 | tail -30
    ;;
  sim)
    hr "simulator (SIMULATED traffic, fictional map), best overnight configuration on one scenario"
    ARGS="${SIM_ARGS:---traffic perfect --whisper-prompt --no-llm}"
    # shellcheck disable=SC2086
    python3 -m sim.run_all $ARGS --only Q5_01_LAND_VS_CROSS --no-docs --out-json "$OUTD/sim.json" 2>&1 | tail -30
    ;;
  *) echo "usage: $0 {finetune|tools|gym|vllm|fastpath|sim}"; exit 2 ;;
esac
