#!/usr/bin/env bash
# Serve Nemotron 3.5 Lightning on the Spark with the model card's own recipe (vLLM v0.27.1), as a SECOND vLLM on port 8002.
# written from the card, NOT RUN. nemoclaw-vllm (port 8000) is untouched. Franco runs this himself.
#
#   venue/serve_lightning.sh                # with the DSpark drafter (card recipe): 33.49B counted -> OVER the 33B cap (tools/budget_check.py)
#   NO_DRAFTER=1 venue/serve_lightning.sh   # without speculative decoding: 30.8B = Lightning 30B (card) + Whisper 0.81B + grammar-only parser -> the only configuration that fits
#
# Memory: the card uses --gpu-memory-utilization 0.85 for Lightning alone. Next to the running 35B that will not fit; stop
# nemoclaw-vllm first (your decision) or lower GPU_UTIL and expect a smaller KV cache. Foreground + --rm under timeout: no docker stop needed.
set -euo pipefail
ROOT="${ROOT:-/mnt/hackssd/GB10_ARSENAL/models}"
MODEL="$ROOT/nemotron-3.5-lightning-nvfp4"
DSPARK="$ROOT/nemotron-3.5-lightning-dspark"
PORT="${PORT:-8002}"; GPU_UTIL="${GPU_UTIL:-0.85}"; HOURS="${HOURS:-6}"
SPEC=(--speculative_config.model /dspark --speculative_config.num_speculative_tokens 3)
MOUNT_DSPARK=(-v "$DSPARK":/dspark:ro)
if [ "${NO_DRAFTER:-0}" = 1 ]; then SPEC=(); MOUNT_DSPARK=(); fi
[ -f "$MODEL/config.json" ] || { echo "Lightning not found at $MODEL"; exit 3; }
echo "Lightning on 127.0.0.1:$PORT  drafter: $([ ${#SPEC[@]} -gt 0 ] && echo yes || echo no)  gpu-memory-utilization $GPU_UTIL"
timeout "${HOURS}h" docker run --rm --gpus all --ipc=host -p 127.0.0.1:"$PORT":8000 \
  -v "$MODEL":/model:ro "${MOUNT_DSPARK[@]}" \
  vllm/vllm-openai:v0.27.1 --model /model --served-model-name "${SERVED_NAME:-lightning}" \
  --moe-backend marlin --kv-cache-dtype fp8 --enable-prefix-caching --gpu-memory-utilization "$GPU_UTIL" \
  --mamba-backend flashinfer --mamba-cache-mode align \
  --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice \
  "${SPEC[@]}"
# Then: python3 venue/tool_loop.py --episode SIM_E1_CROSS_DURING_ROLLOUT --base-url http://127.0.0.1:8002/v1 --model lightning --emit-feed
