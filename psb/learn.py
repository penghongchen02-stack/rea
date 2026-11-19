"""Summaries of settled history that some bots read (View.learned).

Recomputed by the engine every few minutes from the ledger: fills, settlements
and the per-market records the engine keeps under ledger.states["_market"].
Everything here is "in our records": a market counts only once some bot traded
it and it settled.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .markets import category_of

MARKET = "_market"     # ledger.states key holding per-market records
META_BOTS = ("n43-", "n44-")   # bots that copy other bots: never copied themselves


def _parts(slug: str) -> list[str]:
    return slug.split("-")


def league_of(slug: str) -> str:
    p = _parts(slug)
    return p[1] if len(p) > 1 else ""


def event_of(slug: str) -> str:
    return "-".join(_parts(slug)[1:])


def players(slug: str) -> tuple[str, str] | None:
    """(long player, short player) for a two-sided winner market "aec-lg-a-b-date"."""
    p = _parts(slug)
    if p[0] != "aec" or len(p) < 5:
        return None
    return p[2], p[3]


def _t(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def band(price: float) -> int:
    """5c price band: 0 for 0-5c ... 19 for 95-100c."""
    return max(0, min(19, int(price * 20 + 1e-9)))


def learn(led, now: datetime | None = None) -> dict:
    from .livesim import BASE_OF
    from .registry import BOTS

    now = now or datetime.now(timezone.utc)
    recs = led.states.get(MARKET, {})
    settled = led.settled

    # Earliest fill per (slug, side): a price and fair value we can score.
    first: dict[tuple[str, str], dict] = {}
    first_long_fair: dict[str, float] = {}
    for f in led.fills:
        if f.get("fair") is None:
            continue
        first.setdefault((f["slug"], f["side"]), f)
        if f["slug"] not in first_long_fair:
            first_long_fair[f["slug"]] = f["fair"] if f["side"] == "long" else 1 - f["fair"]

    calib = defaultdict(lambda: [0, 0])       # (category, band) -> [wins, n]
    dog = defaultdict(lambda: [0, 0])         # (league, band) -> [wins, n], fair < 50c
    for (slug, side), f in first.items():
        if slug not in settled:
            continue
        won = (settled[slug] == 1) == (side == "long")
        b = band(f["fair"])
        c = calib[(category_of(event_of(slug)), b)]
        c[0] += won
        c[1] += 1
        if f["fair"] < 0.5:
            d = dog[(league_of(slug), b)]
            d[0] += won
            d[1] += 1

    # Per settled winner market: favourite, upset, players, timing.
    wins: dict[str, list[tuple[str, str]]] = defaultdict(list)   # player -> [(iso, league)]
    best_pre: dict[str, float] = {}
    h2h: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)  # pair -> [(date, winner)]
    league_res: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    long_today: set[str] = set()
    today = now.date().isoformat()
    for slug, res in settled.items():
        pl = players(slug)
        rec = recs.get(slug) or {}
        when = led.settled_at.get(slug) or ""
        pre = rec.get("pre", first_long_fair.get(slug))
        if pre is not None and res in (0, 1):
            fav_long = pre >= 0.5
            upset = (res == 1) != fav_long
            league_res[league_of(slug)].append((when, upset))
        if pl is None or res not in (0, 1):
            continue
        winner = pl[0] if res == 1 else pl[1]
        wins[winner].append((when, league_of(slug)))
        date = _parts(slug)[-3:]
        h2h[tuple(sorted(pl))].append(("-".join(date), winner))
        start, last = _t(rec.get("start")), _t(rec.get("last_live"))
        if start and last and last.date().isoformat() == today and last - start >= timedelta(hours=2):
            long_today.update(pl)
    week_ago = now - timedelta(days=7)
    for slug, rec in recs.items():
        pl = players(slug)
        st = _t(rec.get("start"))
        if pl is None or rec.get("pre") is None or st is None or st < week_ago:
            continue
        best_pre[pl[0]] = max(best_pre.get(pl[0], 0.0), rec["pre"])
        best_pre[pl[1]] = max(best_pre.get(pl[1], 0.0), 1 - rec["pre"])
    league_day = {}
    league_last = {}
    for lg, rows in league_res.items():
        rows.sort()
        day = [u for w, u in rows if w.startswith(today)]
        league_day[lg] = (sum(day), len(day))
        league_last[lg] = [u for _, u in rows[-5:]]

    # Bot results by sport, for the bots that copy the best one.
    by_cat = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for key, ms in led.positions.items():
        if key not in BOTS or key.startswith("Live_") or key.startswith(META_BOTS):
            continue
        for slug, p in ms.items():
            if slug in settled and (abs(p.cash) > 1e-9 or abs(p.contracts) > 1e-9):
                r = by_cat[category_of(event_of(slug))][key]
                r[0] += p.cash + p.contracts * settled[slug]
                r[1] += 1
    best_by_cat = {}
    total = defaultdict(lambda: [0.0, 0])
    for cat, rows in by_cat.items():
        ok = {k: r for k, r in rows.items() if r[1] >= 10}
        if ok:
            best_by_cat[cat] = max(ok, key=lambda k: ok[k][0])
        for k, r in rows.items():
            total[k][0] += r[0]
            total[k][1] += r[1]
    ranked = sorted((k for k, r in total.items() if r[1] >= 20), key=lambda k: total[k][0])

    # How much of what a paper bot bought its Live_ copy also got, by league.
    got = defaultdict(lambda: [0.0, 0.0])     # league -> [live contracts, paper contracts]
    per = defaultdict(float)
    for f in led.fills:
        per[(f["bot"], f["slug"])] += f["contracts"]
    twins_of = defaultdict(list)
    for twin, base in BASE_OF.items():
        twins_of[base].append(twin)
    for (b, slug), n in per.items():
        for twin in twins_of.get(b, ()):
            if (twin, slug) in per:
                g = got[league_of(slug)]
                g[0] += per[(twin, slug)]
                g[1] += n
    fill_ratio = {lg: g[0] / g[1] for lg, g in got.items() if g[1] >= 20}

    since = (recs.get("_meta") or {}).get("since")
    return {
        "calib": {k: tuple(v) for k, v in calib.items()},
        "dog": {k: tuple(v) for k, v in dog.items()},
        "wins": dict(wins), "best_pre": best_pre, "h2h": dict(h2h),
        "league_day": league_day, "league_last": league_last, "long_today": long_today,
        "best_by_cat": best_by_cat,
        "best": ranked[-1] if ranked else None, "worst": ranked[0] if ranked else None,
        "fill_ratio": fill_ratio, "since": since,
    }
