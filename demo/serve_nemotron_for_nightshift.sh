#!/usr/bin/env bash
# Serve the Nemotron on the Spark's SSD locally (vLLM), to be registered in Meterless Nightshift as a provider.
#   1. demo/serve_nemotron_for_nightshift.sh            (model from /mnt/hackssd/GB10_ARSENAL/models/nemotron-3.5-lightning-nvfp4)
#   2. Nightshift Console -> Providers -> add an OpenAI-compatible / local provider:
#        base URL http://127.0.0.1:8002/v1   model nemotron-3.5-lightning   (no key needed for the local vLLM)
#   3. export NIGHTSHIFT_API_KEY=<runtime key>; python3 tools/nightshift_smoke.py --models   (the Nemotron id must appear)
#   4. COMPARE_DEADLINE_UTC=23:15 demo/record_open_agent.sh
# GMAPS only ever calls Nightshift (127.0.0.1:8088); Nightshift routes to this Nemotron. model_reported in the traces shows it.
set -euo pipefail
cd "$(dirname "$0")/.."
NO_DRAFTER=1 SERVED_NAME=nemotron-3.5-lightning PORT=${PORT:-8002} venue/serve_lightning.sh
