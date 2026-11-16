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

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `8787` | HTTP port |
| `FEED_TICK_MS` | `15000` | How often simulated odds move |
| `FEED_SEED` | current time | Fix the seed for a reproducible feed |
| `DATA_FILE` | `data/db.json` | Users and bets (JSON file) |
| `INSECURE_COOKIES` | unset | Set `1` to serve production over plain HTTP locally |

## Features

- **Odds screen**: every book's price per outcome, best price highlighted, filter by sport and market (moneyline / spread / total).
- **+EV bets**: each soft-book price compared with the sharp book's (`Sharpline`) no-vig fair line; EV %, fair price, win probability and fractional-Kelly stake. One click logs the bet to your tracker.
- **Arbitrage**: best price per outcome across books; shows stakes for a chosen total and the guaranteed profit.
- **Calculators**: odds converter, no-vig/EV, Kelly, arbitrage, parlay and hedge.
- **Bet tracker**: log bets, settle as won/lost/push, see profit, win rate and ROI.
- **Accounts and plans**: email/password sign-up (scrypt hashes, HTTP-only session cookie). Free accounts see the top 3 +EV bets and the top arbitrage; Pro sees everything.

## Layout

```
shared/odds-math.ts   pure betting math used by both the API and the calculators
server/sample-data.ts simulated feed (fictional books/teams, live price drift)
server/analysis.ts    odds board, +EV and arbitrage detection
server/app.ts         Express API (auth, plans, bets, odds)
server/store.ts       atomic JSON-file persistence
web/                  React + Vite frontend
tests/                math and API workflow tests
```

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/meta` | sports, books, feed version, free-plan limits |
| GET | `/api/odds?sport=&market=` | board rows with best prices |
| GET | `/api/ev?sport=&market=&minEv=` | `minEv` in percent; gated by plan |
| GET | `/api/arbs?sport=&market=` | gated by plan |
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
