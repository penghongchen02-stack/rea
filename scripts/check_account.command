#!/bin/zsh
# Double-click (or `open`) to check the Polymarket US keys and balance in a
# Terminal window. Read-only. The keys are typed here, never saved; the result
# (no keys in it) is also written to live-results/account-check.txt.
cd "$(dirname "$0")/.." || exit 1
mkdir -p live-results
echo "Polymarket US account check (read-only: nothing is traded)."
echo "Paste each key and press Enter. Nothing shows while you paste; that is normal."
echo
read -s "POLYMARKET_KEY_ID?Key ID: " && echo
read -s "POLYMARKET_SECRET_KEY?Secret key: " && echo
export POLYMARKET_KEY_ID POLYMARKET_SECRET_KEY
echo
{ date -u +"Checked %Y-%m-%dT%H:%M:%SZ"; ~/psb-venv/bin/python scripts/check_account.py 2>&1; } | tee live-results/account-check.txt
unset POLYMARKET_KEY_ID POLYMARKET_SECRET_KEY
echo
echo "Done. You can close this window."
