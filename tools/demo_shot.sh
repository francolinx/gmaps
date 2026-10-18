#!/usr/bin/env bash
# tools/demo_shot.sh <name> <url>  -> /tmp/demo_shots/<name>.png + prints the page's status line. Headless Chrome on the Spark.
set -u; mkdir -p /tmp/demo_shots
B=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium-browser || command -v chromium)
"$B" --headless=new --disable-gpu --hide-scrollbars --window-size=1920,1080 --virtual-time-budget=9000 --screenshot="/tmp/demo_shots/$1.png" "$2" >/dev/null 2>&1
"$B" --headless=new --disable-gpu --virtual-time-budget=9000 --dump-dom "$2" 2>/dev/null | grep -o 'page 2026-10-18[^<]*' | head -1
