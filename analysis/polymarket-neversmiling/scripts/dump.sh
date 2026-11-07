#!/bin/bash
# usage: dump.sh <endpoint> <outfile>
EP=$1; OUT=$2; A=0xfcdc071df7080c214196bb0b3b751e5417f9d8e3
CUR=""; N=0; P=0; : > "$OUT"
while true; do
  URL="https://data-api.polymarket.com/v2/$EP?user=$A&limit=1000${CUR:+&cursor=$CUR}"
  for i in 1 2 3 4 5 6; do
    RESP=$(curl -sS -m 120 "$URL") && [ -n "$RESP" ] && break
    echo "retry $i" >&2; sleep $((2**i))
  done
  R=$(printf '%s' "$RESP" | python3 -I -c '
import sys,json
d=json.load(sys.stdin)
rows=d.get("data",[]); pg=d.get("pagination",{})
with open(sys.argv[1],"a") as f:
    for x in rows: f.write(json.dumps(x)+"\n")
print(len(rows), pg.get("next_cursor") or "", (rows[-1]["timestamp"] if rows else 0), "1" if pg.get("has_more") else "0")
' "$OUT") || { echo "parse fail: ${RESP:0:300}" >&2; sleep 5; continue; }
  set -- $R; CNT=$1; CUR=$2; TS=$3; MORE=$4
  N=$((N+CNT)); P=$((P+1))
  [ $((P%25)) -eq 0 ] && echo "$P pages $N rows ts=$TS" >&2
  [ "$CNT" -eq 0 ] || [ -z "$CUR" ] || [ "$MORE" != "1" ] && break
done
echo "DONE $EP $P pages $N rows" >&2
