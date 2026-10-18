#!/usr/bin/env bash
# trainer watchdog (Oct 17, after two Spark power-offs). Every 30 s: time, available memory, GPU temperature and power.
#   available < 15 GB  -> pkill -f local_lora.py (no relaunch; WHY is logged)
#   GPU > 88 C         -> pkill -f local_lora.py, wait 10 min, relaunch the trainer with --resume (at most MAX_RELAUNCH times)
# Never docker stop/kill: the trainer container is --rm and exits when its python dies.
#   nohup train/watchdog.sh >> ~/gmaps_venue/log/watchdog.out 2>&1 &
# Relaunch reads $STATE/run_dir (the run dir) and $STATE/run_args (the local_lora.py args after `--`, one line).
REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${STATE:-$HOME/gmaps_venue/log/watchdog}"; mkdir -p "$STATE"
MIN_AVAIL_GB="${MIN_AVAIL_GB:-15}"; MAX_TEMP_C="${MAX_TEMP_C:-88}"; COOL_S="${COOL_S:-600}"; MAX_RELAUNCH="${MAX_RELAUNCH:-3}"
relaunched=0
echo "$(date '+%F %T') watchdog start pid $$ (avail<${MIN_AVAIL_GB}G kill; temp>${MAX_TEMP_C}C kill+cool ${COOL_S}s+resume, max ${MAX_RELAUNCH})"
while true; do
  avail=$(free -g | awk '/^Mem:/ {print $7}')
  read -r temp power < <(nvidia-smi --query-gpu=temperature.gpu,power.draw --format=csv,noheader,nounits 2>/dev/null | tr -d ',' | head -1)
  temp=${temp:-0}; t=${temp%.*}
  running=0; pgrep -f local_lora.py > /dev/null && running=1
  echo "$(date '+%F %T') avail_GB=$avail gpu_C=$temp power_W=${power:-?} trainer=$running"
  if [ "$running" = 1 ] && [ "$avail" -lt "$MIN_AVAIL_GB" ]; then
    echo "$(date '+%F %T') KILL: available ${avail} GB < ${MIN_AVAIL_GB} GB -> pkill -f local_lora.py (no relaunch: memory, not heat)"
    pkill -f local_lora.py
  elif [ "$running" = 1 ] && [ "$t" -gt "$MAX_TEMP_C" ]; then
    echo "$(date '+%F %T') KILL: GPU ${temp} C > ${MAX_TEMP_C} C -> pkill -f local_lora.py; cooling ${COOL_S}s"
    pkill -f local_lora.py
    sleep "$COOL_S"
    if [ "$relaunched" -lt "$MAX_RELAUNCH" ] && [ -s "$STATE/run_dir" ] && [ -s "$STATE/run_args" ]; then
      relaunched=$((relaunched + 1))
      run_dir=$(cat "$STATE/run_dir"); read -r -a args < "$STATE/run_args"
      echo "$(date '+%F %T') RELAUNCH $relaunched/$MAX_RELAUNCH: OUT=$run_dir ... -- ${args[*]} --resume"
      OUT="$run_dir" HOURS="${HOURS:-4}" nohup "$REPO/train/run_local_lora.sh" 4b -- "${args[@]}" --resume > "$STATE/relaunch_$relaunched.out" 2>&1 &
      sleep 120
    else
      echo "$(date '+%F %T') no relaunch (count $relaunched/$MAX_RELAUNCH or no state files)"
    fi
  fi
  sleep 30
done
