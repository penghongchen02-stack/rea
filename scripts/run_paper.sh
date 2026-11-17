#!/usr/bin/env bash
# One scheduled paper-trading run. Paper only: no orders are ever sent.
# Usage: scripts/run_paper.sh [minutes]   (default 110)
# Reads and writes paper-results/ledger.json, then commits and pushes the
# ledger and report so the next run (possibly in a fresh container) resumes.
set -euo pipefail
cd "$(dirname "$0")/.."
MINUTES="${1:-110}"
BRANCH="${PSB_BRANCH:-claude/funny-hopper-9tzyeh}"
mkdir -p paper-results
exec 9>paper-results/.lock
flock -n 9 || { echo "another paper run is active; skipping"; exit 0; }
git pull --ff-only origin "$BRANCH" || true
# The replay data is not needed for paper trading, only the league list.
mkdir -p data && python3 -I -c "import json,urllib.request;open('data/sports.json','w').write(json.dumps(json.load(urllib.request.urlopen(urllib.request.Request('https://gamma-api.polymarket.com/sports',headers={'User-Agent':'psb-research/0.1'}),timeout=60))))" 2>/dev/null \
  || mkdir -p data
python3 -m psb.paper --minutes "$MINUTES" --ledger paper-results/ledger.json > paper-results/report.md.tmp
mv paper-results/report.md.tmp paper-results/report.md
git add paper-results
git -c user.name="psb" -c user.email="noreply@anthropic.com" \
  commit -q -m "chore(paper): results through $(date -u +%Y-%m-%dT%H:%MZ)" || true
for i in 1 2 3 4; do git push origin "HEAD:$BRANCH" && break; sleep $((2 ** i)); done
tail -n 12 paper-results/report.md
