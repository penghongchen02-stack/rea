#!/usr/bin/env python3
"""pairbot results (paper only)."""
import json, os, collections
H = os.path.dirname(os.path.abspath(__file__))
st = json.load(open(os.path.join(H, "state.json"))) if os.path.exists(os.path.join(H, "state.json")) else {"cash": 0, "bankroll": 0, "held": {}, "stats": {}}
ev = [json.loads(l) for l in open(os.path.join(H, "events.jsonl"))] if os.path.exists(os.path.join(H, "events.jsonl")) else []
pairs = [e for e in ev if e["ev"] == "pair"]; firsts = [e for e in ev if e["ev"] == "first"]; lost = [e for e in ev if e["ev"] == "unpaired_settled"]
stops = [e for e in ev if e["ev"] == "stopped"]
pp = sum(e["profit"] for e in pairs); up = sum(e["profit"] for e in lost); sp = sum(e["profit"] for e in stops)
print(f"Paper balance: started ${st.get('bankroll', 0):,.2f} | now ${st['cash']:,.2f} (as of {st.get('saved', '?')})")
print(f"First legs filled: {len(firsts)}")
if pairs:
    sh = sum(e["shares"] for e in pairs)
    print(f"Pairs completed: {len(pairs)} ({sh} shares) | average pair cost {sum(e['pair_cost'] * e['shares'] for e in pairs) / sh:.4f} | profit ${pp:+.2f}")
else:
    print("Pairs completed: 0")
if lost:
    print(f"Legs that never paired and went to the final result: {len(lost)} ({sum(1 for e in lost if e['profit'] > 0)} won) | profit ${up:+.2f}")
else:
    print("Legs that never paired and went to the final result: 0")
print(f"Lone sides closed early by the stop: {len(stops)} | profit ${sp:+.2f}" + (f" (average {sp / len(stops):+.2f} each)" if stops else ""))
held = st.get("held", {})
print(f"Legs waiting for their other side now: {len(held)} (${sum(x['cost'] for x in held.values()):.2f})")
print(f"TOTAL on finished business: ${pp + up + sp:+.2f}")
by = collections.defaultdict(lambda: [0, 0.0, 0, 0.0])
for e in pairs: by[e["league"]][0] += 1; by[e["league"]][1] += e["profit"]
for e in lost + stops: by[e["league"]][2] += 1; by[e["league"]][3] += e["profit"]
if by:
    print("\nBy league: pairs (profit) | lone sides stopped or settled (profit)")
    for lg, v in sorted(by.items(), key=lambda kv: -(kv[1][1] + kv[1][3])):
        print(f"  {lg:10} {v[0]:4d} ({v[1]:+7.2f}) | {v[2]:3d} ({v[3]:+7.2f})")
print("\nCounters:", st.get("stats"))
for e in ev[-8:]:
    print("  ", e["t"][11:19], e["ev"], e["slug"][:40], e.get("side") or e.get("first_side"), "profit" if "profit" in e else "px", e.get("profit", e.get("px")))
# --- markouts (simulation check copies only)
mk = [e for e in ev if e["ev"] == "markout"]
if mk:
    print("\nAfter-fill check: fair price of the side we bought, minus the fair price when we were filled")
    print("  (negative = price moved against us right after the fill: the fills a real trader would pick us off on)")
    for leg in ("first", "hedge"):
        for h in (5, 30, 60):
            xs = [e["move"] for e in mk if e["leg"] == leg and e["h"] == h]
            if xs:
                bad = sum(1 for x in xs if x < -0.005)
                print(f"  {leg:5} +{h:2}s: {len(xs):3} fills | average move {100 * sum(xs) / len(xs):+5.2f}c | "
                      f"moved 0.5c+ against us {100 * bad / len(xs):4.0f}%")
