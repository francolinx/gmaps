#!/usr/bin/env bash
# Train the GMAPS parser LoRA locally on the DGX Spark inside NVIDIA's PyTorch container (no Unsloth, nothing to compile).
# Runs as YOUR user (files stay yours), under `timeout`, with --rm: no docker stop/rm needed afterwards.
#
#   train/run_local_lora.sh 4b                 # Qwen3-4B-Instruct-2507 (safe config), 2 epochs on pairs_asr.jsonl
#   train/run_local_lora.sh 1.7b               # Qwen3-1.7B (Lightning config)
#   train/run_local_lora.sh 4b --smoke         # 20 steps on 200 rows, no merge: proves the path in a few minutes
#   train/run_local_lora.sh 4b -- --epochs 1 --variants clean,unprompted,prompted   # anything after -- goes to local_lora.py
#
# Env overrides: ROOT (SSD models root), OUT_BASE (where runs land), MIN_FREE_GB (memory guard, default 45), IMAGE, HOURS (timeout, default 3),
#   OUT (an EXISTING run dir under OUT_BASE to reuse, e.g. with `-- ... --resume`; train.log is appended).
# Never run while another training job is running. It is fine next to nemoclaw-vllm as long as the guard passes; if it OOMs, the
# container just exits and nothing else is touched.
set -euo pipefail
KEY="${1:-}"; shift || true
SMOKE=0; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --smoke) SMOKE=1; shift ;;
    --) shift; EXTRA=("$@"); break ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${ROOT:-/mnt/hackssd/GB10_ARSENAL/models}"
OUT_BASE="${OUT_BASE:-$HOME/gmaps_venue/adapters}"
IMAGE="${IMAGE:-nvcr.io/nvidia/pytorch:25.11-py3}"
MIN_FREE_GB="${MIN_FREE_GB:-45}"
HOURS="${HOURS:-3}"
case "$KEY" in
  4b)   BASE_DIR="$ROOT/reasoning/qwen3-4b-instruct";  RUN="gmaps-parser-4b" ;;   # Qwen3-4B-Instruct-2507 (MODEL_MANIFEST path)
  1.7b) BASE_DIR="$ROOT/qwen3-1.7b";         RUN="gmaps-parser-1.7b" ;;
  *) echo "usage: $0 {4b|1.7b} [--smoke] [-- extra local_lora.py args]"; exit 2 ;;
esac
if [ ! -f "$BASE_DIR/config.json" ]; then
  echo "base model not found at $BASE_DIR (config.json missing). Folders under $ROOT:"; ls "$ROOT" | sed 's/^/  /'; exit 3
fi
DATA="$REPO/eval/synthetic/train_ft/pairs_asr.jsonl"
MAN="$REPO/eval/synthetic/train_ft/ASR_NOISE_MANIFEST.json"
if [ ! -f "$DATA" ] || ! python3 - "$DATA" "$MAN" <<'EOF'
import hashlib, json, sys
d, m = sys.argv[1], json.load(open(sys.argv[2]))
sys.exit(0 if hashlib.sha256(open(d, 'rb').read()).hexdigest() == m['pairs_asr_jsonl']['sha256'] else 1)
EOF
then
  echo "pairs_asr.jsonl missing or stale vs its manifest: regenerating (train/augment_asr_noise.py, deterministic)"
  (cd "$REPO" && python3 train/augment_asr_noise.py > /dev/null)
fi
AVAIL=$(free -g | awk '/^Mem:/ {print $7}')
if [ "$AVAIL" -lt "$MIN_FREE_GB" ]; then
  echo "memory guard: only ${AVAIL} GB available (< ${MIN_FREE_GB} GB). Free memory first (or MIN_FREE_GB=... to override). Not starting."; exit 4
fi
if [ -n "${OUT:-}" ]; then
  OUT="$(cd "$OUT" 2>/dev/null && pwd)" || { echo "OUT=$OUT is not an existing directory"; exit 5; }
  [ "$(dirname "$OUT")" = "$(cd "$OUT_BASE" && pwd)" ] || { echo "OUT must be a run dir directly under OUT_BASE ($OUT_BASE)"; exit 5; }
else
  STAMP=$(date +%Y%m%d-%H%M%S)
  OUT="$OUT_BASE/$RUN-$STAMP"
fi
mkdir -p "$OUT" "$HOME/.cache/ft-pyuser" "$HOME/.cache/ft-pip"
ARGS=(--base "/models/${BASE_DIR#"$ROOT"/}" --data /workspace/gmaps/eval/synthetic/train_ft/pairs_asr.jsonl --out "/out/$(basename "$OUT")")
if [ "$SMOKE" = 1 ]; then ARGS+=(--max-steps 20 --limit 200 --no-merge); fi
ARGS+=("${EXTRA[@]}")
echo "run: $RUN  base: $BASE_DIR  out: $OUT  image: $IMAGE  available: ${AVAIL} GB  timeout: ${HOURS}h"
echo "args: ${ARGS[*]}"
# --user: files in $OUT stay owned by you. pip installs go to a persistent user base, so the second run works offline.
timeout "${HOURS}h" docker run --rm --gpus all --ipc=host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONUSERBASE=/pyuser -e PIP_CACHE_DIR=/pipcache -e HF_HUB_OFFLINE=1 -e TOKENIZERS_PARALLELISM=false -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -v "$REPO":/workspace/gmaps -v "$ROOT":/models:ro -v "$OUT_BASE":/out -v "$HOME/.cache/ft-pyuser":/pyuser -v "$HOME/.cache/ft-pip":/pipcache \
  "$IMAGE" bash -lc "
    python -m pip install --user -q 'transformers==4.57.1' 'peft==0.17.1' 'accelerate>=1.5,<2' 'safetensors>=0.4' 2>&1 | tail -2   # Oct 17: peft >= 0.18 refuses this image's torchao 0.14; this pair worked here
    python -c 'import torch, transformers, peft; print(\"torch\", torch.__version__, \"cuda\", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"-\", \"transformers\", transformers.__version__, \"peft\", peft.__version__)'
    python /workspace/gmaps/train/local_lora.py ${ARGS[*]}
  " 2>&1 | tee -a "$OUT/train.log"
echo
echo "report: $OUT/report.json"; [ -f "$OUT/report.json" ] && python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(json.dumps({k:r[k] for k in ('rows','steps','first_loss','last_loss','train_runtime_s','peak_gpu_mem_gib','adapter','merged_fp16')}, indent=1))" "$OUT/report.json"
cat <<EOF

Serve (second vLLM, new port; nemoclaw-vllm untouched), adapter form:
  docker run --rm --gpus all -p 127.0.0.1:8001:8000 -v $BASE_DIR:/base:ro -v $OUT/adapter:/adapter:ro vllm/vllm-openai:v0.27.1 \\
    --model /base --served-model-name base --enable-lora --lora-modules gmaps-parser=/adapter --max-lora-rank 16 --gpu-memory-utilization 0.15
Or the merged copy:  --model $OUT/merged_fp16 --served-model-name gmaps-parser
Then: python3 venue/llm_fallback.py --airport KLGA --base-url http://127.0.0.1:8001/v1 --model gmaps-parser --text "<transmission>"
EOF
