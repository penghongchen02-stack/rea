#!/usr/bin/env bash
# Paper-only pairbot launcher for cloud sessions. Usage: pairbots/run.sh now|before [minutes]
# Reads the Polymarket US key from PM_US_KEY_ID / PM_US_SECRET_KEY (set in the environment's
# settings), writes it to a private temp file only while the bot runs, and deletes it after.
set -euo pipefail
which="${1:?now or before}"; minutes="${2:-110}"
here="$(cd "$(dirname "$0")" && pwd)"; repo="$(cd "$here/.." && pwd)"
: "${PM_US_KEY_ID:?PM_US_KEY_ID not set in this environment}"
: "${PM_US_SECRET_KEY:?PM_US_SECRET_KEY not set in this environment}"
keys="$(mktemp)"; chmod 600 "$keys"; trap 'rm -f "$keys"' EXIT
python3 -c 'import json,os,sys; json.dump({"key_id":os.environ["PM_US_KEY_ID"],"secret_key":os.environ["PM_US_SECRET_KEY"]},open(sys.argv[1],"w"))' "$keys"
cd "$here/pairbot-$which"
PAIR_PSB="$repo" PAIR_KEYS="$keys" PAIR_STOP="${PAIR_STOP:-0}" timeout "$((minutes*60))" python3 pairbot.py || [ $? -eq 124 ]
python3 pair_report.py
