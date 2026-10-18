#!/usr/bin/env bash
# Whisper large-v3-turbo on rendered synthetic audio (SIMULATED), in the same offline throwaway container as the demo.
#   eval/synthetic/asr.sh dev KATL           -> eval/synthetic/dev/katl/<scenario>.asr.json (text only)
#   eval/synthetic/asr.sh dev KATL --prompt  -> <scenario>.asr_prompt.json (Whisper initial prompt: airlines + chart vocabulary)
#   eval/synthetic/asr.sh dev KATL --prompt --words -> <scenario>.asr_prompt_words.json (+ per-word confidence)
set -euo pipefail
SPLIT=$1; ICAO=$(echo "$2" | tr 'A-Z' 'a-z'); REPO=$(cd "$(dirname "$0")/../.." && pwd)
PROMPT_ENV=(); SUFFIX=asr
if [ "${3:-}" = "--prompt" ]; then PROMPT_ENV=(-e "GMAPS_ASR_PROMPT_ICAO=$2"); SUFFIX=asr_prompt; fi
if [ "${4:-}" = "--words" ]; then PROMPT_ENV+=(-e GMAPS_ASR_WORDS=1); SUFFIX=${SUFFIX}_words; fi   # + per-word confidence
IMG=sha256:46591c6e4a018d8d197fa246b1e3d682c907654aab4e9402302abb3e6a7dd916
OUT=$(mktemp -d); chmod 777 "$OUT"
timeout 900 docker run --rm --network none --gpus all --ipc=host -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HOME=/tmp "${PROMPT_ENV[@]}" --user "$(id -u):$(id -g)" \
  -v "$REPO:/gmaps:ro" -v "$HOME/gmaps_venue/synthetic/$SPLIT/$ICAO:/audio:ro" -v "$HOME/gmaps_models/whisper-large-v3-turbo:/models/whisper:ro" -v "$OUT:/out" \
  --entrypoint python3 "$IMG" /gmaps/eval/synthetic/asr_dir.py > /dev/null 2>&1
python3 - "$OUT/asr.json" "$REPO/eval/synthetic/$SPLIT/$ICAO" "$SUFFIX" <<'PY'
import json, sys
from pathlib import Path
d = json.load(open(sys.argv[1])); by = {}
for k, v in d.items():
    by.setdefault(k.rsplit('_', 1)[0], {})[k] = v
for sid, m in by.items():
    (Path(sys.argv[2]) / f'{sid}.{sys.argv[3]}.json').write_text(json.dumps(m, indent=1) + '\n')
print(f'{len(d)} clips, {len(by)} scenarios -> {sys.argv[2]}')
PY
