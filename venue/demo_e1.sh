#!/usr/bin/env bash
# One command for the video: E1 through the full stack. Alerts are a dry run (post_alert returns dry_run_not_sent):
# the Telegram destination was dropped after the event, so nothing is sent.
exec "$(dirname "$0")/demo.sh" SIM_E1_CROSS_DURING_ROLLOUT --no-alerts
