#!/usr/bin/env bash
# CPU-only cloud box setup: Python deps only, into ./.venv (the airport pipeline already looks for .venv/bin/python).
# No model downloads, no Kokoro, no Whisper, no GPU packages, no services. Safe to re-run.
#   bash tools/cloud/setup.sh
# Then:  CUDA_VISIBLE_DEVICES="" .venv/bin/python -m pytest -m "not spark"
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 12), f"Python >= 3.12 needed, have {sys.version}"'
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r tools/cloud/requirements.txt
.venv/bin/python -c 'import numpy, PIL, pdfminer, jsonschema, yaml, pytest; print("python deps OK")'
# System tools the airport pipeline calls. Not installed here (Python deps only): report what is missing.
missing=0
for t in pdftoppm node git; do command -v "$t" >/dev/null || { echo "MISSING system tool: $t"; missing=1; }; done
command -v google-chrome >/dev/null || command -v chromium >/dev/null || { echo "MISSING system tool: google-chrome or chromium"; missing=1; }
[ "$missing" = 0 ] && echo "system tools OK" || echo "Install the missing tools in the environment image (poppler-utils, chromium, nodejs); see docs/CLOUD_QUEUE.md"
