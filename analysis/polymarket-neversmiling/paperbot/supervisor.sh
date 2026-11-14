#!/bin/bash
# Keeps the paper-trading engine alive for ~7000 s; restarts it if the process is gone.
D=/home/user/rea/analysis/polymarket-neversmiling/paperbot
end=$(( $(date +%s) + 7000 ))
while [ "$(date +%s)" -lt "$end" ]; do
  if ! pgrep -f "python3 -u -m paperbot.run" >/dev/null; then
    echo "$(date -u +%FT%TZ) starting engine"
    (cd "$D" && PAPERBOT_DATA="$D/paperbot-data" PAPERBOT_PORT=8080 PAPERBOT_ACTIVE=all nohup python3 -u -m paperbot.run >> "$D/paperbot-data-stdout.log" 2>&1 &)
  fi
  sleep 30
done
echo "$(date -u +%FT%TZ) supervisor window ended"
