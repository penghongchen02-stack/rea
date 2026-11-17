# LineScout

An odds-analysis web app: an odds screen across sportsbooks, a +EV finder, an
arbitrage finder, betting calculators, a bet tracker, and free/pro accounts.

**Everything runs on simulated sample data.** Teams, sportsbooks and prices are
fictional. Billing is a demo that switches plans without taking payment.

## Run it

```sh
npm install
npm run dev        # API on :8787 + Vite on :5173 (open http://localhost:5173)
```

Production-style single server:

```sh
npm run build
npm start          # serves the API and the built UI on :8787
```

Checks: `npm run typecheck` and `npm test`.

`npm run build:demo` writes `dist-demo/linescout.html`, a single page that runs the feed and tools in the browser (accounts and bets in localStorage).

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8787` | HTTP port |
| `FEED_TICK_MS` | `15000` | How often simulated odds move |
| `FEED_SEED` | current time | Fix the seed for a reproducible feed |
| `DATA_FILE` | `data/db.json` | Users and bets (JSON file) |
| `INSECURE_COOKIES` | unset | Set `1` to serve production over plain HTTP locally |

## Features

- **Compare Odds**: every book's line and price for moneylines, spreads, totals and team totals, by period (halves, quarters, first period, first five innings). The best price on the main line is highlighted; books hanging a different number are marked.
- **Player Props** and **Futures**: book-by-book over/unders per player and stat, and championship prices ranked by best odds.
- **+EV Bets**: prices beating a fair line made by removing the margin from every book and averaging (needs at least three books on the same line).
- **Sharp +EV**: the same, against the sharp book's (`Sharpline`) no-vig line only.
- **Arbitrage** and **Low Hold**: best prices per line ranked by combined margin, with equal-payout stakes; negative hold is an arbitrage.
- **Middles**: opposite sides at different lines from two books, with the gap, result if the middle hits or misses, and the break-even hit rate.
- **Moving Lines**: each book's largest price change on its line since opening, with the best price still available.
- **Calculators** (odds converter, no-vig/EV, Kelly, arbitrage, parlay, hedge) and a **Bet Tracker** (profit, win rate, ROI).
- **Shared filters**: sport, market, period, a saved sportsbook selection and an auto-refresh switch.
- **Accounts and plans**: email/password sign-up (scrypt hashes, HTTP-only session cookie). Free accounts preview the top results of each edge tool; Pro sees every result (up to 200 rows per request).

## Layout

```
shared/odds-math.ts   pure betting math used by both the API and the calculators
server/sample-data.ts simulated feed (fictional books/teams, live price drift)
server/analysis.ts    odds board, +EV, arbitrage, low hold, middles, line moves
server/service.ts     read-only tool routes shared by the API and the demo
server/rules.ts       validation, plan gating, bet stats
server/app.ts         Express API (auth, plans, bets, tools)
server/store.ts       atomic JSON-file persistence
web/                  React + Vite frontend (web/src/demo: in-browser backend)
tests/                math and API workflow tests
```

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/meta` | sports with periods and prop stats, games, books, free-plan limits |
| GET | `/api/odds?sport=&market=&period=&event=&prop=&books=` | board rows with best prices |
| GET | `/api/ev?…&minEv=&method=market\|sharp` | `minEv` in percent; gated by plan |
| GET | `/api/arbs?…`, `/api/low-hold?…` | gated by plan |
| GET | `/api/middles?…` | gated by plan |
| GET | `/api/moves?…&minMove=` | `minMove` in percentage points; gated by plan |
| POST | `/api/auth/signup`, `/api/auth/login`, `/api/auth/logout` | `{ email, password }` |
| GET | `/api/auth/me` | current user or `null` |
| POST | `/api/billing/checkout`, `/api/billing/cancel` | demo plan switch |
| GET/POST | `/api/bets` | list with stats / create |
| PATCH/DELETE | `/api/bets/:id` | settle / remove |

## Going live

To use real odds, replace `SampleFeed` with an adapter for a licensed odds data
provider that produces the same `SportEvent` and `MarketLine` shapes
(`server/types.ts`). The analysis, API and UI need no changes. For real
payments, swap the `/api/billing/*` handlers for a payment provider's checkout
and webhook flow. For multiple server instances, move sessions and the JSON
store to a database.
