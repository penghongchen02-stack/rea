#!/bin/bash
# Keeps the paper-trading engine alive for ~7000 s; restarts it if the process is gone.
D="$(cd "$(dirname "$0")" && pwd)"      # wherever this copy lives (the cloud session or a laptop)
end=$(( $(date +%s) + 7000 ))
while [ "$(date +%s)" -lt "$end" ]; do
  if ! pgrep -f "python3 -u -m paperbot.run" >/dev/null; then
    echo "$(date -u +%FT%TZ) starting engine"
    (cd "$D" && PAPERBOT_DATA="$D/paperbot-data" PAPERBOT_PORT=8080 PAPERBOT_ACTIVE=all nohup python3 -u -m paperbot.run >> "$D/paperbot-data-stdout.log" 2>&1 &)
  fi
  # the real-money bots' own processes (paperbot/live.py): shadow mode unless armed by hand. "live" is Bitcoin only
  # on port 8095; "live4" is all four coins on port 8096, for comparison.
  for spec in "live 8095" "live4 8096"; do
    if ! pgrep -f "python3 -u -m paperbot.live run $spec" >/dev/null; then
      echo "$(date -u +%FT%TZ) starting live bot $spec"
      (cd "$D" && PAPERBOT_DATA="$D/paperbot-data" nohup python3 -u -m paperbot.live run $spec >> "$D/paperbot-data-live-stdout.log" 2>&1 &)
    fi
  done
  # shadow xarb_fast (paperbot/livexarb.py; it cannot send orders): "xarb" is Bitcoin only on port 8097, "xarb4" all four coins on 8098
  for spec in "xarb 8097" "xarb4 8098"; do
    if ! pgrep -f "python3 -u -m paperbot.livexarb run $spec" >/dev/null; then
      echo "$(date -u +%FT%TZ) starting shadow xarb $spec"
      (cd "$D" && PAPERBOT_DATA="$D/paperbot-data" nohup python3 -u -m paperbot.livexarb run $spec >> "$D/paperbot-data-live-stdout.log" 2>&1 &)
    fi
  done
  sleep 30
done
echo "$(date -u +%FT%TZ) supervisor window ended"
