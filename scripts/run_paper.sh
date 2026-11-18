#!/usr/bin/env bash
# One scheduled paper-trading run. Paper only: no orders are ever sent.
# Usage: scripts/run_paper.sh [minutes|auto]   (default auto)
#   auto: run until 3 minutes before the next scheduled start (every 2 hours at
#   :17 UTC), capped at 115 minutes so it always finishes inside the
#   2-hour limit on background jobs.
# Reads and writes paper-results/ledger.json, then commits and pushes the
# ledger and report so the next run (possibly in a fresh container) resumes.
set -euo pipefail
cd "$(dirname "$0")/.."
MINUTES="${1:-auto}"
if [ "$MINUTES" = auto ]; then
  MINUTES=$(python3 -I -c "
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc)
t = now.replace(minute=14, second=0, microsecond=0)
while t.hour % 2 or t - now < timedelta(minutes=5):
    t += timedelta(hours=1)
# Background jobs are stopped after 2 hours, so a run must finish (and push)
# well inside that; a late restart just runs until the next scheduled start.
print(min(115.0, round((t - now).total_seconds() / 60, 1)))")
fi
# Live games every 5 s and upcoming games every 60 s; US requests stay under
# the client's 12 req/s throttle (venue limit 25 req/s), so busy slates poll
# as fast as that allows.
FAST="${PSB_FAST:-5}"
SLOW="${PSB_SLOW:-60}"
BRANCH="${PSB_BRANCH:-claude/funny-hopper-9tzyeh}"
mkdir -p paper-results
exec 9>paper-results/.lock
flock -n 9 || { echo "another paper run is active; skipping"; exit 0; }
git pull --ff-only origin "$BRANCH" || true
# The replay data is not needed for paper trading, only the league list.
mkdir -p data && python3 -I -c "import json,urllib.request;open('data/sports.json','w').write(json.dumps(json.load(urllib.request.urlopen(urllib.request.Request('https://gamma-api.polymarket.com/sports',headers={'User-Agent':'psb-research/0.1'}),timeout=60))))" 2>/dev/null \
  || mkdir -p data
# Polymarket US's sport tree: which sport each US league belongs to.
python3 -I -c "import json,urllib.request;open('data/us_sports.json','w').write(json.dumps(json.load(urllib.request.urlopen(urllib.request.Request('https://gateway.polymarket.us/v2/sports',headers={'User-Agent':'psb-research/0.1'}),timeout=60))))" 2>/dev/null || true
python3 -m psb.paper --minutes "$MINUTES" --fast "$FAST" --slow "$SLOW" --ledger paper-results/ledger.json > paper-results/report.md.tmp
mv paper-results/report.md.tmp paper-results/report.md
# Data files for the dashboard (bots, settled games, recent fills).
python3 -m psb.dashboard --ledger paper-results/ledger.json --out paper-results/dashboard || true
git add paper-results
git -c user.name="psb" -c user.email="noreply@anthropic.com" \
  commit -q -m "chore(paper): results through $(date -u +%Y-%m-%dT%H:%MZ)" || true
for i in 1 2 3 4; do git push origin "HEAD:$BRANCH" && break; sleep $((2 ** i)); done
tail -n 12 paper-results/report.md
