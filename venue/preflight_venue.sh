#!/usr/bin/env bash
# Venue preflight (Raleigh, Oct 17): read-only checks on one screen. Changes nothing, starts nothing.
#
#   venue/preflight_venue.sh               # checks; reads the last tests/run_all.py result from ~/gmaps_venue/log/run_all_last.txt
#   venue/preflight_venue.sh --run-tests   # also re-runs tests/run_all.py first and refreshes that file
#
# Env: PORT (adapter server, default 8001), ROOT (models root), RUN (adapter run dir; default: newest full gmaps-parser-4b-* run, not a smoke).
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8001}"
ROOT="${ROOT:-/mnt/hackssd/GB10_ARSENAL/models}"
BASE="$ROOT/reasoning/qwen3-4b-instruct"
LAST=~/gmaps_venue/log/run_all_last.txt
if [ -z "${RUN:-}" ]; then
  # newest full run: an adapter plus a report.json with > 100 steps (skips the 20-step smoke runs)
  RUN=$(ls -dt ~/gmaps_venue/adapters/gmaps-parser-4b-*/ 2>/dev/null | while read -r d; do
    [ -f "$d/adapter/adapter_config.json" ] && python3 -c "import json,sys; sys.exit(json.load(open(sys.argv[1]))['steps'] <= 100)" "$d/report.json" 2>/dev/null && { echo "${d%/}"; break; }; done)
fi
fails=0
ok()   { printf '  OK    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fails=$((fails + 1)); }
info() { printf '  ..    %s\n' "$*"; }

echo "GMAPS venue preflight  $(date '+%Y-%m-%d %H:%M %Z')  branch $(git branch --show-current) @ $(git log -1 --format=%h)"
[ -f "$BASE/config.json" ] && ok "base model   $BASE" || bad "base model missing: $BASE"
if [ -n "$RUN" ] && [ -f "$RUN/adapter/adapter_config.json" ]; then
  ok "adapter      $RUN/adapter"
  [ -f "$RUN/report.json" ] && info "train report $(python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(f\"rows {r['rows']}, steps {r['steps']}, loss {r['first_loss']:.3f}->{r['last_loss']:.3f}\")" "$RUN/report.json")" || info "no report.json in $RUN"
else
  bad "no adapter under ~/gmaps_venue/adapters/gmaps-parser-4b-*/adapter"
fi
for img in vllm/vllm-openai:v0.27.1 nvcr.io/nvidia/pytorch:25.11-py3; do
  docker image inspect "$img" >/dev/null 2>&1 && ok "image        $img" || bad "image missing: $img"
done
if models=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/v1/models"); then
  ids=$(python3 -c "import json,sys; print(' '.join(m['id'] for m in json.loads(sys.argv[1])['data']))" "$models" 2>/dev/null)
  case " $ids " in *" gmaps-parser "*) ok "port $PORT    serving: $ids";; *) bad "port $PORT answers but gmaps-parser is not listed ($ids)";; esac
else
  bad "port $PORT not answering (venue/start_demo.sh starts it)"
fi
curl -s --max-time 3 http://127.0.0.1:8000/v1/models >/dev/null && info "port 8000    nemoclaw-vllm (35B reference) up" || info "port 8000    down (35B reference rows DEFERRED; Franco: docker start nemoclaw-vllm)"
if [ "${1:-}" = "--run-tests" ]; then
  python3 tests/run_all.py > /tmp/gmaps_run_all.$$ 2>&1; tail -1 /tmp/gmaps_run_all.$$ | sed "s/^/$(date '+%m-%d %H:%M') /" > "$LAST"; rm -f /tmp/gmaps_run_all.$$
fi
if [ -f "$LAST" ]; then
  grep -q '139/139' "$LAST" && ok "tests        $(cat "$LAST")" || bad "tests        $(cat "$LAST")"
else
  info "tests        no saved result (run with --run-tests)"
fi
[ -f docs/SCOREBOARD.md ] && [ -f docs/scoreboard.html ] && ok "scoreboard   docs/SCOREBOARD.md, docs/scoreboard.html" || bad "scoreboard missing (python3 tools/scoreboard.py)"
n=$(ls docs/demo_captures/*.txt 2>/dev/null | wc -l)
[ "$n" -ge 6 ] && ok "captures     $n in docs/demo_captures/" || bad "captures     $n of 6 in docs/demo_captures/"
avail=$(free -g | awk '/Mem:/{print $7}')
[ "$avail" -ge 20 ] && ok "memory       ${avail} GB available" || bad "memory       only ${avail} GB available"
echo "$([ $fails -eq 0 ] && echo 'PREFLIGHT OK' || echo "PREFLIGHT: $fails problem(s)")"
exit $([ $fails -eq 0 ] && echo 0 || echo 1)
