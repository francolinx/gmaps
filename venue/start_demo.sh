#!/usr/bin/env bash
# Start the demo stack (Raleigh, Oct 17): the 4B base + gmaps-parser LoRA on one vLLM (port 8001), in the
# background under `timeout 8h` with --rm (no docker stop needed afterwards), wait for /v1/models, run the preflight, print the demo commands.
# nemoclaw-vllm (8000) is never touched.
#
#   venue/start_demo.sh              # start (or reuse) the 8001 server
#   venue/start_demo.sh --with-35b   # also print the line Franco runs for the 35B reference server (this script never runs it)
#
# Env: PORT (default 8001), ROOT (models root), RUN (adapter run dir; default: the preflight's choice), HOURS (default 8).
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8001}"; HOURS="${HOURS:-8}"
[ "$PORT" = 8000 ] && { echo "port 8000 belongs to nemoclaw-vllm (even when it is stopped); use 8001, 8003 or 8004"; exit 3; }
ROOT="${ROOT:-/mnt/hackssd/GB10_ARSENAL/models}"
BASE="$ROOT/reasoning/qwen3-4b-instruct"
if [ -z "${RUN:-}" ]; then
  RUN=$(ls -dt ~/gmaps_venue/adapters/gmaps-parser-4b-*/ 2>/dev/null | while read -r d; do
    [ -f "$d/adapter/adapter_config.json" ] && python3 -c "import json,sys; sys.exit(json.load(open(sys.argv[1]))['steps'] <= 100)" "$d/report.json" 2>/dev/null && { echo "${d%/}"; break; }; done || true)
fi
[ -n "${RUN:-}" ] && [ -f "$RUN/adapter/adapter_config.json" ] || { echo "no full-run adapter found (set RUN=...)"; exit 2; }
LOG=~/gmaps_venue/log/vllm_parser_serve_$(date +%Y%m%d-%H%M).out

if curl -s --max-time 3 "http://127.0.0.1:$PORT/v1/models" | grep -q '"gmaps-parser"'; then
  echo "port $PORT already serving gmaps-parser: reusing it"
elif ss -ltn | grep -q ":$PORT "; then
  echo "port $PORT is taken by something that is not the gmaps-parser server; pick another: PORT=8003 $0"; exit 3
else
  NAME=gmaps-parser-serve-$(date +%H%M%S)
  echo "starting $NAME on 127.0.0.1:$PORT (adapter $RUN/adapter, timeout ${HOURS}h, log $LOG)"
  nohup timeout "${HOURS}h" docker run --rm --name "$NAME" --gpus all -p 127.0.0.1:$PORT:8000 \
    -v "$BASE":/base:ro -v "$RUN/adapter":/adapter:ro \
    vllm/vllm-openai:v0.27.1 --model /base --served-model-name base --enable-lora --lora-modules gmaps-parser=/adapter --max-lora-rank 16 \
    --max-model-len 8192 --gpu-memory-utilization 0.15 --enable-auto-tool-choice --tool-call-parser hermes \
    > "$LOG" 2>&1 &
  for i in $(seq 1 72); do   # engine init was 113 s ; allow 6 min
    curl -s --max-time 3 "http://127.0.0.1:$PORT/v1/models" | grep -q '"gmaps-parser"' && break
    sleep 5
  done
  curl -s --max-time 3 "http://127.0.0.1:$PORT/v1/models" | grep -q '"gmaps-parser"' || { echo "server not up after 6 min; tail -20 $LOG"; exit 4; }
  echo "up after ~$((i * 5)) s"
fi

venue/preflight_venue.sh || true
cat <<EOF

Demo commands (each about 60 s; expected output in docs/demo_captures/<track>.txt; runbook docs/DEMO_RUNBOOK_RALEIGH.md):
  demo/run_track.sh finetune
  demo/run_track.sh tools
  demo/run_track.sh gym
  demo/run_track.sh vllm
  demo/run_track.sh fastpath
  demo/run_track.sh sim
EOF
if [ "${1:-}" = "--with-35b" ]; then
  echo
  echo "35B reference server (Franco runs this himself; it is over the 33B cap and is never the judged configuration):"
  echo "  docker start nemoclaw-vllm     # then wait for: curl -s http://127.0.0.1:8000/v1/models"
fi
