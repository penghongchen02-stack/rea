import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { americanToDecimal, devig, expectedValue } from "../shared/odds-math.js";
import { FREE_LIMITS, createApp } from "../server/app.js";
import { BOOKS, SampleFeed } from "../server/sample-data.js";
import { JsonStore } from "../server/store.js";
import type { MarketLine } from "../server/types.js";

let server: Server;
let base: string;
const feed = new SampleFeed(42, () => Date.parse("2026-01-01T00:00:00Z"));

beforeAll(async () => {
  const app = createApp({ feed, store: await JsonStore.open(null) });
  server = app.listen(0);
  await new Promise((resolve) => server.once("listening", resolve));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(() => new Promise<void>((resolve) => server.close(() => resolve())));

class Client {
  private cookie = "";
  async call(method: string, path: string, body?: unknown): Promise<{ status: number; json: any }> {
    const res = await fetch(base + path, {
      method,
      headers: { "content-type": "application/json", cookie: this.cookie },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const setCookie = res.headers.get("set-cookie");
    if (setCookie) this.cookie = setCookie.split(";")[0]!;
    return { status: res.status, json: res.status === 204 ? null : await res.json() };
  }
}

async function proClient(email: string): Promise<Client> {
  const client = new Client();
  await client.call("POST", "/api/auth/signup", { email, password: "password1" });
  await client.call("POST", "/api/billing/checkout", {});
  return client;
}

function market(id: string): MarketLine {
  return feed.listMarkets().find((m) => m.id === id)!;
}

describe("sample feed", () => {
  it("is deterministic for a seed and moves on tick", () => {
    const a = new SampleFeed(7, () => 0);
    const b = new SampleFeed(7, () => 0);
    expect(a.listMarkets()).toEqual(b.listMarkets());
    const before = JSON.stringify(a.listMarkets());
    a.tick();
    expect(a.version).toBe(1);
    expect(JSON.stringify(a.listMarkets())).not.toBe(before);
  });

  it("covers periods, team totals, props and futures", () => {
    const kinds = new Set(feed.listMarkets().map((m) => `${m.kind}/${m.period}`));
    for (const k of ["moneyline/full", "spread/h1", "total/q1", "total/p1", "moneyline/f5", "team_total/full", "player_prop/full", "future/full"]) {
      expect(kinds).toContain(k);
    }
  });

  it("never lets one book's own prices form an arbitrage, and keeps each book on its opening line", () => {
    for (let seed = 1; seed <= 15; seed++) {
      const f = new SampleFeed(seed, () => 0, 4);
      for (let t = 0; t < 4; t++) f.tick();
      for (const m of f.listMarkets()) {
        for (const [bookId, offer] of Object.entries(m.offers)) {
          if (!offer) continue;
          const implied = offer.prices.reduce((sum, p) => sum + 1 / americanToDecimal(p), 0);
          expect(implied).toBeGreaterThan(1);
          expect(offer.point).toBe(m.opening[bookId]!.point);
        }
      }
    }
  });
});

describe("compare odds", () => {
  it("highlights the best price among books on the main line", async () => {
    const { json } = await new Client().call("GET", "/api/odds?sport=basketball&market=spread&period=full");
    expect(json.rows.length).toBeGreaterThan(0);
    for (const row of json.rows) {
      expect(row.event.sportId).toBe("basketball");
      row.market.outcomes.forEach((_: string, i: number) => {
        const main = Object.values(row.market.offers as Record<string, { point: number; prices: number[] } | null>)
          .filter((o) => o !== null && o.point === row.market.point)
          .map((o) => americanToDecimal(o!.prices[i]!));
        expect(americanToDecimal(row.best[i].price)).toBe(Math.max(...main));
      });
    }
  });

  it("limits columns and best prices to the chosen books", async () => {
    const { json } = await new Client().call("GET", "/api/odds?market=moneyline&books=northbet,goldline");
    for (const row of json.rows) for (const best of row.best) for (const id of best.bookIds) expect(["northbet", "goldline"]).toContain(id);
  });

  it("rejects unknown market kinds and periods", async () => {
    expect((await new Client().call("GET", "/api/odds?market=parlays")).status).toBe(400);
    expect((await new Client().call("GET", "/api/odds?period=q9")).status).toBe(400);
  });
});

describe("edge tools", () => {
  it("prices sharp EV against the sharp book's no-vig line on the same number", async () => {
    const pro = await proClient("sharp@example.com");
    const { json } = await pro.call("GET", "/api/ev?minEv=0&method=sharp");
    const sharp = BOOKS.find((b) => b.sharp)!;
    expect(json.items.length).toBeGreaterThan(0);
    for (const op of json.items.slice(0, 20)) {
      expect(op.bookId).not.toBe(sharp.id);
      const m = market(op.market.id);
      const ref = m.offers[sharp.id]!;
      expect(ref.point).toBe(op.point);
      expect(m.offers[op.bookId]!.point).toBe(op.point);
      const fair = devig(ref.prices.map(americanToDecimal));
      expect(op.ev).toBeCloseTo(expectedValue(fair[op.outcomeIndex]!, americanToDecimal(op.price)));
    }
  });

  it("builds the market-average fair line from at least three books", async () => {
    const pro = await proClient("market@example.com");
    const { json } = await pro.call("GET", "/api/ev?minEv=0&method=market");
    expect(json.items.length).toBeGreaterThan(0);
    for (const op of json.items) expect(op.fairSources).toBeGreaterThanOrEqual(3);
  });

  it("lists arbitrages as the negative-hold end of low hold", async () => {
    const pro = await proClient("hold@example.com");
    const low = (await pro.call("GET", "/api/low-hold")).json;
    const arbs = (await pro.call("GET", "/api/arbs")).json;
    const holds = low.items.map((r: { hold: number }) => r.hold);
    expect(holds).toEqual([...holds].sort((a: number, b: number) => a - b));
    expect(arbs.total).toBe(low.items.filter((r: { hold: number }) => r.hold < 0).length);
    for (const arb of arbs.items) {
      const shares = arb.legs.reduce((s: number, l: { stakeShare: number }) => s + l.stakeShare, 0);
      expect(shares).toBeCloseTo(1);
      for (const leg of arb.legs) expect(americanToDecimal(leg.price) * leg.stakeShare).toBeCloseTo(1 / (1 + arb.hold));
    }
  });

  it("finds middles only where both sides can win", async () => {
    const pro = await proClient("middles@example.com");
    const { json } = await pro.call("GET", "/api/middles");
    expect(json.items.length).toBeGreaterThan(0);
    for (const row of json.items) {
      const [a, b] = row.legs;
      expect(a.bookId).not.toBe(b.bookId);
      expect(row.gap).toBeGreaterThan(0);
      expect(row.gap).toBe(row.market.kind === "spread" ? a.point - b.point : b.point - a.point);
      expect(row.breakEven).toBeGreaterThanOrEqual(0);
      expect(row.breakEven).toBeLessThan(1);
    }
  });

  it("reports line moves on an unchanged line, largest first", async () => {
    const pro = await proClient("moves@example.com");
    const { json } = await pro.call("GET", "/api/moves?minMove=1");
    expect(json.items.length).toBeGreaterThan(0);
    const sizes = json.items.map((m: { impliedChange: number }) => Math.abs(m.impliedChange));
    expect(sizes).toEqual([...sizes].sort((x: number, y: number) => y - x));
    for (const move of json.items.slice(0, 20)) {
      const m = market(move.market.id);
      expect(m.opening[move.bookId]!.prices[move.outcomeIndex]).toBe(move.from);
      expect(Math.abs(move.impliedChange)).toBeGreaterThanOrEqual(0.01);
    }
  });
});

describe("accounts, plans and bets", () => {
  it("previews each tool for free users and unlocks it on upgrade", async () => {
    const client = new Client();
    const signup = await client.call("POST", "/api/auth/signup", { email: "Ana@Example.com", password: "correct horse" });
    expect(signup.status).toBe(201);
    expect(signup.json.user).toMatchObject({ email: "ana@example.com", plan: "free" });
    expect(signup.json.user.passwordHash).toBeUndefined();

    const tools = { "/api/ev?minEv=0": FREE_LIMITS.ev, "/api/low-hold": FREE_LIMITS.lowHold, "/api/middles": FREE_LIMITS.middles, "/api/moves": FREE_LIMITS.moves };
    for (const [path, limit] of Object.entries(tools)) {
      const free = (await client.call("GET", path)).json;
      expect(free.items.length).toBeLessThanOrEqual(limit);
      expect(free.locked).toBe(free.total - free.items.length);
    }

    await client.call("POST", "/api/billing/checkout", {});
    for (const path of Object.keys(tools)) {
      const pro = (await client.call("GET", path)).json;
      expect(pro.locked).toBe(0);
      expect(pro.items.length).toBe(Math.min(pro.total, 200));
    }
  });

  it("rejects duplicate signups and bad passwords", async () => {
    const client = new Client();
    await client.call("POST", "/api/auth/signup", { email: "dup@example.com", password: "password1" });
    expect((await client.call("POST", "/api/auth/signup", { email: "dup@example.com", password: "password1" })).status).toBe(409);
    expect((await new Client().call("POST", "/api/auth/login", { email: "dup@example.com", password: "wrongpass" })).status).toBe(401);
    expect((await new Client().call("POST", "/api/auth/login", { email: "dup@example.com", password: "password1" })).status).toBe(200);
  });

  it("tracks bets per user and computes profit", async () => {
    const alice = new Client();
    const bob = new Client();
    await alice.call("POST", "/api/auth/signup", { email: "alice@example.com", password: "password1" });
    await bob.call("POST", "/api/auth/signup", { email: "bob@example.com", password: "password1" });

    const win = (await alice.call("POST", "/api/bets", { description: "Metro Comets ML", book: "NorthBet", americanOdds: 150, stake: 100 })).json.bet;
    const loss = (await alice.call("POST", "/api/bets", { description: "Over 224.5", book: "Goldline", americanOdds: -110, stake: 55 })).json.bet;
    await alice.call("PATCH", `/api/bets/${win.id}`, { status: "won" });
    await alice.call("PATCH", `/api/bets/${loss.id}`, { status: "lost" });

    const { json } = await alice.call("GET", "/api/bets");
    expect(json.stats).toMatchObject({ total: 2, settled: 2, won: 1, staked: 155, profit: 95 });

    expect((await bob.call("PATCH", `/api/bets/${win.id}`, { status: "lost" })).status).toBe(404);
    expect((await bob.call("GET", "/api/bets")).json.bets).toEqual([]);
    expect((await alice.call("POST", "/api/bets", { description: "x", book: "y", americanOdds: 50, stake: 1 })).status).toBe(400);
    expect((await new Client().call("GET", "/api/bets")).status).toBe(401);

    expect((await alice.call("DELETE", `/api/bets/${loss.id}`)).status).toBe(204);
    expect((await alice.call("GET", "/api/bets")).json.bets).toHaveLength(1);
  });
});
