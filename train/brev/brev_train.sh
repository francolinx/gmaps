#!/usr/bin/env bash
# Train the GMAPS parser LoRA on an NVIDIA Brev GPU instance (fallback when the Spark cannot train in time). Cloud prep, Oct 17.
# TRAINING ONLY: the adapter comes back to the Spark; inference stays on the Spark (GMAPS's LocalLLM refuses non-local endpoints).
#
# On the Brev instance (any CUDA 12 image with Python >= 3.10; 1x L40S / A100 / H100 is plenty):
#   bash brev_train.sh <hf-user>/gmaps-trainkit-private            # kit = private HF dataset repo made by brev_kit.sh on the Spark
#   bash brev_train.sh /path/to/trainkit                             # or a directory copied with scp
# Env: BASE (default Qwen/Qwen3-4B-Instruct-2507), EPOCHS (2), BATCH (8), MAXLEN (1280), OUT (~/gmaps_out), HF_TOKEN (for a private kit).
# Result: $OUT/<run>/adapter + merged_fp16 + report.json, tarred to $OUT/<run>.tar.gz, and (if HF_TOKEN) uploaded to <hf-user>/<run>-private.
set -euo pipefail
KIT="${1:?kit: <hf-user>/<dataset-repo> or a local directory}"
BASE="${BASE:-Qwen/Qwen3-4B-Instruct-2507}"
EPOCHS="${EPOCHS:-2}"; BATCH="${BATCH:-8}"; MAXLEN="${MAXLEN:-1280}"
OUT="${OUT:-$HOME/gmaps_out}"; mkdir -p "$OUT" "$HOME/models"
STAMP=$(date +%Y%m%d-%H%M%S); RUN="gmaps-parser-$(basename "$BASE" | tr 'A-Z' 'a-z')-brev-$STAMP"

nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "no GPU visible"; exit 3; }
python3 -m venv "$HOME/ftvenv" 2>/dev/null || true
# shellcheck disable=SC1091
source "$HOME/ftvenv/bin/activate"
python -m pip install -q -U pip
python -m pip install -q "torch" "transformers>=4.51,<6" "peft>=0.15" "accelerate>=1.5" "safetensors>=0.4" "huggingface_hub>=0.34"
python -c 'import torch; print("torch", torch.__version__, "cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0))'

# 1. the kit: local_lora.py + pairs_asr.jsonl + ASR_NOISE_MANIFEST.json (nothing else from the repo is needed)
if [ -d "$KIT" ]; then KITDIR="$KIT"; else
  KITDIR="$HOME/trainkit"; hf download "$KIT" --repo-type dataset --local-dir "$KITDIR" ${HF_TOKEN:+--token "$HF_TOKEN"}
fi
test -f "$KITDIR/local_lora.py" && test -f "$KITDIR/pairs_asr.jsonl" || { echo "kit incomplete in $KITDIR"; ls -la "$KITDIR"; exit 4; }
python - "$KITDIR/pairs_asr.jsonl" "$KITDIR/ASR_NOISE_MANIFEST.json" <<'EOF'
import hashlib, json, sys
want = json.load(open(sys.argv[2]))['pairs_asr_jsonl']['sha256']; got = hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest()
print('data sha256', 'OK' if want == got else f'MISMATCH want {want[:12]} got {got[:12]}'); sys.exit(0 if want == got else 5)
EOF

# 2. the base model, as a LOCAL directory (local_lora.py refuses Hub ids on purpose)
BASEDIR="$HOME/models/$(basename "$BASE")"
[ -f "$BASEDIR/config.json" ] || hf download "$BASE" --local-dir "$BASEDIR"

# 3. train (same script, same defaults as the Spark run; bf16, gradient checkpointing, completion-only loss)
python "$KITDIR/local_lora.py" --base "$BASEDIR" --data "$KITDIR/pairs_asr.jsonl" --out "$OUT/$RUN" \
  --epochs "$EPOCHS" --batch "$BATCH" --max-len "$MAXLEN" "${@:2}" 2>&1 | tee "$OUT/$RUN.log"
python -c "import json,sys; r=json.load(open(sys.argv[1])); print(json.dumps({k:r.get(k) for k in ('rows','steps','first_loss','last_loss','train_runtime_s','peak_gpu_mem_gib','device')}, indent=1))" "$OUT/$RUN/report.json"

# 4. bring it home: tarball (scp) and/or a PRIVATE HF model repo
tar -C "$OUT" -czf "$OUT/$RUN.tar.gz" "$RUN/adapter" "$RUN/report.json" "$RUN/rendered_example.txt"
echo "adapter tarball: $OUT/$RUN.tar.gz  ($(du -h "$OUT/$RUN.tar.gz" | cut -f1))"
if [ -n "${HF_TOKEN:-}" ] && [ -n "${HF_USER:-}" ]; then
  hf repos create "$HF_USER/$RUN-private" --type model --private --exist-ok --token "$HF_TOKEN"
  hf upload "$HF_USER/$RUN-private" "$OUT/$RUN/adapter" adapter --repo-type model --token "$HF_TOKEN"
  hf upload "$HF_USER/$RUN-private" "$OUT/$RUN/report.json" report.json --repo-type model --token "$HF_TOKEN"
  echo "on the Spark:  ~/ftvenv/bin/hf download $HF_USER/$RUN-private --local-dir ~/gmaps_venue/adapters/$RUN   (then serve ~/gmaps_venue/adapters/$RUN/adapter)"
else
  echo "on your laptop: scp <brev-instance>:$OUT/$RUN.tar.gz .   then copy to the Spark and: tar -xzf $RUN.tar.gz -C ~/gmaps_venue/adapters/"
fi
