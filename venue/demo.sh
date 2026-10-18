#!/usr/bin/env bash
# GMAPS demo, one command (venue code). Usage: venue/demo.sh [EPISODE] [--alerts|--no-alerts]
#   1. Whisper large-v3-turbo on the episode's owned clips (throwaway offline container); if that fails, use transcript text
#   2. restart the GMAPS MCP tool server with a clean ledger, fresh run ID and the input mode
#   3. one OpenClaw `gmaps` agent turn per transmission (Qwen3.6 via inference.local -> gmaps MCP tools [-> post_alert])
#   4. write the UI timeline JSON and print the URL
set -euo pipefail
EP=${1:-SIM_E1_CROSS_DURING_ROLLOUT}
ALERTS=${2:---alerts}
shift $(( $# < 2 ? $# : 2 ))
EXTRA=("$@")   # e.g. --tick 38 --tick 44
REPO=$(cd "$(dirname "$0")/.." && pwd)
V=$HOME/gmaps_venue
IMG=sha256:46591c6e4a018d8d197fa246b1e3d682c907654aab4e9402302abb3e6a7dd916   # existing nemoclaw vLLM image
TAG=$(echo "$EP" | sed -E 's/^SIM_(E[0-9]+)_.*/\1/' | tr 'A-Z' 'a-z')
# one run at a time: every run restarts the single tool server on :11435, so overlapping runs mix their tool calls
exec 9>"$V/log/demo.lock"
flock -n 9 || { echo "another demo run holds $V/log/demo.lock; not starting"; exit 75; }
RUN=${TAG}demo-$(date +%Y%m%d-%H%M%S)-$(head -c2 /dev/urandom | od -An -tx1 | tr -d " \n")   # random suffix: IDs never repeat or look alike
echo "== run $RUN  episode $EP  alerts $ALERTS"

# 1. ASR (fallback: transcript text, labelled)
python3 "$REPO/venue/build_runtime.py"
ASR_DIR=$V/log/asr_$RUN; mkdir -p "$ASR_DIR"
MODE="transcript text (no owned clips or Whisper failed)"; ASR_ARG=()
if ls "$V/audio/${EP}_"*.wav >/dev/null 2>&1 && timeout 600 docker run --rm --network none --gpus all --ipc=host \
     -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HOME=/tmp --user "$(id -u):$(id -g)" \
     -v "$REPO:/gmaps:ro" -v "$V/audio:/audio:ro" -v "$HOME/gmaps_models/whisper-large-v3-turbo:/models/whisper:ro" -v "$ASR_DIR:/out" \
     --entrypoint python3 "$IMG" /gmaps/venue/asr_clips.py "${EP}_" 2>&1 | grep -E '\.wav|median' ; then
  if [ -s "$ASR_DIR/asr_results.json" ]; then
    NCLIP=$(ls "$V/audio/${EP}_"*.wav | wc -l); NLINE=$(python3 -c "import json;print(len(json.load(open('$REPO/episodes/$EP.transcript.json'))['events']))")
    if [ "$NCLIP" -ge "$NLINE" ]; then MODE="Whisper ASR of owned re-voiced clips"
    else MODE="mixed: Whisper ASR for $NCLIP owned clip(s), transcript text for the other $((NLINE-NCLIP)) line(s)"; fi
    ASR_ARG=(--asr-results "$ASR_DIR/asr_results.json" --fill-missing-with-script)
  fi
fi
echo "== input mode: $MODE"

# 2. clean tool server (after ASR so the alert template knows the input mode)
rm -f "$V/runtime/feed/$EP.json"
PID=$(ss -ltnp 2>/dev/null | grep '172.18.0.1:11435' | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)
if [ -n "${PID:-}" ]; then kill "$PID"; sleep 1; fi
SEND=""; [ "$ALERTS" = "--alerts" ] && SEND="--send-alerts"
( cd "$V/runtime" && GMAPS_MCP_TOKEN=$(cat "$V/secrets/mcp_token") GMAPS_RUN_ID=$RUN \
  exec setsid python3 venue/gmaps_mcp.py --episode "$EP" --bind 172.18.0.1 --port 11435 $SEND --input-mode "$MODE" ) < /dev/null > "$V/log/mcp_server_$RUN.log" 2>&1 9>&- &
for i in $(seq 1 20); do curl -s -m 1 http://172.18.0.1:11435/health | grep -q "$RUN" && break; sleep 0.5; done
curl -s -m 2 http://172.18.0.1:11435/health | grep -q "$RUN" || { echo "tool server did not start"; exit 1; }

# 3. agent turns
python3 "$REPO/venue/run_fullstack.py" "$EP" --run-id "$RUN" "${ASR_ARG[@]}" "${EXTRA[@]}"

# 4. UI
# timing/evaluation runs set GMAPS_TIMELINE_DIR (e.g. ~/gmaps_venue/log/timing_runs): their timelines stay out of the repo
# and out of the :8766 display copy, and agent_latest.json is left alone
if [ -n "${GMAPS_TIMELINE_DIR:-}" ]; then
  mkdir -p "$GMAPS_TIMELINE_DIR"; TL="$GMAPS_TIMELINE_DIR/agent_$RUN.json"
  python3 "$REPO/venue/build_timeline.py" "$RUN" --episode "$EP" --input-mode "$MODE" --out "$TL"
else
  python3 "$REPO/venue/build_timeline.py" "$RUN" --episode "$EP" --input-mode "$MODE" --out "$REPO/ui_scaffold/samples/agent_$RUN.json"
  cp "$REPO/ui_scaffold/samples/agent_$RUN.json" "$REPO/ui_scaffold/samples/agent_latest.json"
fi
ss -ltn | grep -q ':8765 ' || ( cd "$REPO/ui_scaffold" && exec setsid python3 -m http.server 8765 --bind 127.0.0.1 ) < /dev/null > "$V/log/ui_http.log" 2>&1 9>&- &
echo "$RUN" > "$V/log/current_run_id"
[ -n "${GMAPS_TIMELINE_DIR:-}" ] && echo "== run $RUN done. Timeline: $TL" || echo "== run $RUN done. Open: http://localhost:8765/?f=samples/agent_$RUN.json"
