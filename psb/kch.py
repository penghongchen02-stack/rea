"""Bots built from the PR&R report on Polymarket wallet kch123 (Jan 1-Mar 30, 2026):
https://www.polyresearchrobotics.com/reports/kch123 . Paper money, $1,000 each.

What the report measured, and how each point is used here:

  sports      edge in college football (+38% ROI), NFL (+33.5%) and men's college
              basketball (+22.5%); NHL modest (+10.1%); NBA, UFC and non-sports
              lose. These bots trade only CFB, NFL and CBB.
  price       strongest at a 40-70c entry; weak under 20c and thin over 70c.
              These bots buy only at 40-70c.
  style       99.9% buys, many small fills per market, held to the result.
              Here: $5 buys, up to 6 per market ($30, the usual 3% cap); no selling.
  markets     moneylines, spreads (his biggest market was an NFL spread) and totals.
  hours       weakest 4-6pm, 8pm and 10pm. The report does not name a time zone;
              kch123-v3-hours assumes US Eastern and makes no new buys in those hours.

The report does not say how kch123 picks a side. As with the other bots here, the
international Polymarket price stands in for his read: a buy needs the US ask to be
at least 1c under it after the fee.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from .bots import SIDES
from .simple_bots import Simple

ET = ZoneInfo("America/New_York")
WEAK_HOURS_ET = frozenset({16, 17, 20, 22})   # 4-6pm, 8pm, 10pm


class Kch123(Simple):
    key, idea, title = "kch123-v3", 0, "kch123-v3: CFB, NFL, CBB at 40-70c, many small buys"
    cats = frozenset({"cfb", "nfl", "cbb"})
    kinds = ("moneyline", "spreads", "totals")
    price_floor, price_cap = 0.40, 0.70
    clip, max_entries = 5.0, 6
    edge = 0.01

    def pick(self, v, st):
        best = None
        for s in SIDES:
            a, e = v.ask(s), v.taker_edge(s)
            if a is None or e is None or e < self.edge or not self.price_floor <= a <= self.price_cap:
                continue
            if best is None or e > best[1]:
                best = (s, e)
        return (best[0], self.edge) if best else None


class Kch123Hours(Kch123):
    key, title = "kch123-v3-hours", "kch123-v3 without his weak hours (4-6pm, 8pm, 10pm US Eastern)"

    def pick(self, v, st):
        if v.now.astimezone(ET).hour in WEAK_HOURS_ET:
            return None
        return super().pick(v, st)


KCH_CLASSES = (Kch123, Kch123Hours)
KCH = tuple(c() for c in KCH_CLASSES)
