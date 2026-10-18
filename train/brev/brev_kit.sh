#!/usr/bin/env bash
# Build the minimal training kit for Brev and (optionally) push it to a PRIVATE Hugging Face dataset repo. Run on the Spark.
# Cloud prep, Oct 17. The kit holds exactly three files: train/local_lora.py, pairs_asr.jsonl (synthetic, 25 MB), ASR_NOISE_MANIFEST.json.
#
#   train/brev/brev_kit.sh                       # -> ~/gmaps_venue/brev_trainkit/ + a tarball; prints the upload command
#   HF_USER=<you> train/brev/brev_kit.sh --push  # also uploads to <you>/gmaps-trainkit-private (private; delete after the event)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
KIT="${KIT:-$HOME/gmaps_venue/brev_trainkit}"
mkdir -p "$KIT"
DATA="$REPO/eval/synthetic/train_ft/pairs_asr.jsonl"
[ -f "$DATA" ] || (cd "$REPO" && python3 train/augment_asr_noise.py > /dev/null)
cp "$REPO/train/local_lora.py" "$REPO/eval/synthetic/train_ft/ASR_NOISE_MANIFEST.json" "$DATA" "$REPO/train/brev/brev_train.sh" "$KIT/"
python3 - "$REPO" "$KIT" <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'tools/hooks'))
import precommit_check as PC
bad = []
for f in sorted(Path(sys.argv[2]).iterdir()):
    if f.is_file() and f.suffix != '.gz':
        probs = [p for p in PC.check(f.name, f.read_bytes(), PC.private_hashes()) if 'larger than 5 MB' not in p]
        bad += [f'{f.name}: {p}' for p in probs]
        if b'audio_real_private' in f.read_bytes() or b'INTERNAL' in f.read_bytes():
            bad.append(f'{f.name}: private marker')
if bad:
    print('refusing to build the kit:'); [print('  - ' + b) for b in bad]; sys.exit(2)
print('kit scan clean')
EOF
tar -C "$(dirname "$KIT")" -czf "$KIT.tar.gz" "$(basename "$KIT")"
echo "kit: $KIT  ($(du -sh "$KIT" | cut -f1)), tarball $KIT.tar.gz"
if [ "${1:-}" = "--push" ]; then
  : "${HF_USER:?set HF_USER}"
  ~/ftvenv/bin/hf repos create "$HF_USER/gmaps-trainkit-private" --type dataset --private --exist-ok
  ~/ftvenv/bin/hf upload "$HF_USER/gmaps-trainkit-private" "$KIT" . --repo-type dataset --exclude "*.tar.gz"
  echo "on Brev:  HF_TOKEN=<token> bash brev_train.sh $HF_USER/gmaps-trainkit-private"
else
  echo "to push:  HF_USER=<you> $0 --push      or copy $KIT.tar.gz with scp"
fi
