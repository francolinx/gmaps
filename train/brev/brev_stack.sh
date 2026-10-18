#!/usr/bin/env bash
# Brev "whole stack" fallback for a laptop-only day (addendum 2 item C.0.2). Syntax-checked, NOT run on a real Brev box.
# Run INSIDE `brev shell <instance>` (a GPU VM with docker + NVIDIA runtime): the GMAPS runtime, the vLLM server and the demo commands
# all live on that one box, and inference stays on its 127.0.0.1 (LocalLLM refuses non-local URLs; keep it that way).
#
#   bash brev_stack.sh                                    # code from the private GitHub repo (needs GH access on the box)
#   BUNDLE=~/repo.bundle bash brev_stack.sh               # code from the SSD bundle, after: scp <ssd>/raleigh_2026-10-17/repo.bundle <instance>:~/
#   RUN=gmaps-parser-4b-<stamp> HF_USER=darkstar008 HF_TOKEN=<token> bash brev_stack.sh   # adapter from Franco's PRIVATE HF mirror
#   ADAPTER_TAR=~/adapter.tar.gz bash brev_stack.sh       # or an adapter tarball copied with scp
#
# Env: REPO_URL (private GitHub URL), BRANCH (default weekend/parser-ft), PORT (default 8001), MODELS (default ~/models), HOURS (server timeout, 8).
set -euo pipefail
BRANCH="${BRANCH:-weekend/parser-ft}"; PORT="${PORT:-8001}"; HOURS="${HOURS:-8}"
MODELS="${MODELS:-$HOME/models}"; BASE="$MODELS/qwen3-4b-instruct"
WORK="${WORK:-$HOME/gmaps}"; ADIR="$HOME/adapters"
IMAGE="vllm/vllm-openai:v0.27.1"
say() { printf '\n== %s\n' "$*"; }

say "0. checks"
command -v docker > /dev/null || { echo "docker missing: pick a Brev image with docker + the NVIDIA runtime"; exit 2; }
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "no GPU visible"; exit 2; }
[ "$PORT" = 8000 ] && { echo "keep 8000 free (the Spark convention: 8000 = nemoclaw-vllm); use 8001"; exit 2; }

say "1. code -> $WORK ($BRANCH)"
if [ -d "$WORK/.git" ]; then
  git -C "$WORK" fetch -q --all 2>/dev/null || true; git -C "$WORK" checkout -q "$BRANCH"
elif [ -n "${BUNDLE:-}" ]; then
  git clone -q "$BUNDLE" "$WORK" -b "$BRANCH"
else
  : "${REPO_URL:?set REPO_URL (private GitHub repo) or BUNDLE=<path to repo.bundle>}"
  git clone -q "$REPO_URL" "$WORK" -b "$BRANCH"
fi
git -C "$WORK" log --oneline -1

say "2. tools: venv with huggingface_hub (hf) and nemo-gym"
python3 -m venv "$HOME/gmvenv"; "$HOME/gmvenv/bin/pip" install -q -U pip 'huggingface_hub[cli]' nemo-gym
"$HOME/gmvenv/bin/python" -c "import importlib.metadata as m; print('nemo-gym', m.version('nemo-gym'))"
HF="$HOME/gmvenv/bin/hf"

say "3. base model -> $BASE"
[ -f "$BASE/config.json" ] || "$HF" download Qwen/Qwen3-4B-Instruct-2507 --local-dir "$BASE"

say "4. adapter"
mkdir -p "$ADIR"
if [ -n "${ADAPTER_TAR:-}" ]; then
  tar -xzf "$ADAPTER_TAR" -C "$ADIR"; RUN="${RUN:-$(ls -t "$ADIR" | head -1)}"
else
  : "${RUN:?set RUN=<adapter run name> (and HF_USER, HF_TOKEN in the environment) or ADAPTER_TAR=<tarball>}"; : "${HF_USER:?set HF_USER}"
  "$HF" download "$HF_USER/$RUN-private" --local-dir "$ADIR/$RUN"      # HF_TOKEN from the environment; never written to a file
fi
AD="$ADIR/$RUN/adapter"; [ -f "$AD/adapter_config.json" ] || { echo "no adapter_config.json under $AD"; exit 3; }

say "5. serve on 127.0.0.1:$PORT (same command as the Spark's Phase 3)"
if curl -s --max-time 3 "http://127.0.0.1:$PORT/v1/models" | grep -q '"gmaps-parser"'; then
  echo "already serving"
else
  nohup timeout "${HOURS}h" docker run --rm --name "gmaps-parser-serve-$(date +%H%M%S)" --gpus all -p "127.0.0.1:$PORT:8000" \
    -v "$BASE":/base:ro -v "$AD":/adapter:ro "$IMAGE" --model /base --served-model-name base --enable-lora \
    --lora-modules gmaps-parser=/adapter --max-lora-rank 16 --max-model-len 8192 --gpu-memory-utilization 0.5 \
    --enable-auto-tool-choice --tool-call-parser hermes > "$HOME/vllm_parser_serve.out" 2>&1 &
  for _ in $(seq 72); do curl -s --max-time 3 "http://127.0.0.1:$PORT/v1/models" | grep -q '"gmaps-parser"' && break; sleep 5; done
fi
curl -s "http://127.0.0.1:$PORT/v1/models" | python3 -c "import json,sys; print('models:', [m['id'] for m in json.load(sys.stdin)['data']])"

say "6. one-shot check"
cd "$WORK"
python3 venue/llm_fallback.py --airport KLGA --base-url "http://127.0.0.1:$PORT/v1" --model gmaps-parser \
  --text "Delta five sixteen, runway four, cleared for takeoff" || echo "(one-shot failed: see $HOME/vllm_parser_serve.out)"

cat <<EOF

== ready. Demo commands (run on this box, in $WORK):
  python3 tests/run_all.py
  demo/run_track.sh finetune | tools | gym | vllm | fastpath       # if demo/ exists on this branch; else docs/DEMO_RUNBOOK_RALEIGH.md
  python3 venue/tool_loop.py --episode SIM_E1_CROSS_DURING_ROLLOUT --base-url http://127.0.0.1:$PORT/v1 --model base --emit-feed
  python3 -m sim.run_all --text --only Q5_01_LAND_VS_CROSS --no-docs
  $HOME/gmvenv/bin/gym eval aggregate ...                            # on gym/rollouts (see docs/DEMO_RUNBOOK_RALEIGH.md)
Not available here: the Kokoro audio simulator and the real-time Whisper path (show docs/demo_captures/ instead).
Afterwards: stop the server (docker stop gmaps-parser-serve-...), DELETE the Brev instance, delete the private HF adapter repo after the event.
EOF
