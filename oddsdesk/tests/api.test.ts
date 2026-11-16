import type { AddressInfo } from "node:net";
import type { Server } from "node:http";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { americanToDecimal, devig, expectedValue } from "../shared/odds-math.js";
import { FREE_LIMITS, createApp } from "../server/app.js";
import { BOOKS, SampleFeed } from "../server/sample-data.js";
import { JsonStore } from "../server/store.js";

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
});

describe("odds API", () => {
  it("serves the board with best prices", async () => {
    const { json } = await new Client().call("GET", "/api/odds?sport=basketball&market=moneyline");
    expect(json.rows.length).toBeGreaterThan(0);
    for (const row of json.rows) {
      expect(row.event.sportId).toBe("basketball");
      row.market.outcomes.forEach((_: string, i: number) => {
        const offered = Object.values(row.market.prices as Record<string, number[] | null>)
          .filter((p): p is number[] => p !== null)
          .map((p) => americanToDecimal(p[i]!));
        expect(americanToDecimal(row.best[i].price)).toBe(Math.max(...offered));
      });
    }
  });

  it("rejects unknown market kinds", async () => {
    const { status } = await new Client().call("GET", "/api/odds?market=props");
    expect(status).toBe(400);
  });

  it("prices EV against the sharp book's no-vig line", async () => {
    const { json } = await new Client().call("GET", "/api/ev?minEv=0");
    const sharp = BOOKS.find((b) => b.sharp)!;
    const op = json.items[0];
    const market = feed.listMarkets().find((m) => m.id === op.marketId)!;
    const fair = devig(market.prices[sharp.id]!.map(americanToDecimal));
    const index = market.outcomes.indexOf(op.outcome);
    expect(op.ev).toBeCloseTo(expectedValue(fair[index]!, americanToDecimal(op.price)));
    expect(op.ev).toBeGreaterThanOrEqual(0);
  });
});

describe("accounts, plans and bets", () => {
  it("gates results for free users and unlocks them on upgrade", async () => {
    const client = new Client();
    const signup = await client.call("POST", "/api/auth/signup", { email: "Ana@Example.com", password: "correct horse" });
    expect(signup.status).toBe(201);
    expect(signup.json.user).toMatchObject({ email: "ana@example.com", plan: "free" });
    expect(signup.json.user.passwordHash).toBeUndefined();

    const free = (await client.call("GET", "/api/ev?minEv=0")).json;
    expect(free.items.length).toBeLessThanOrEqual(FREE_LIMITS.ev);
    expect(free.locked).toBe(free.total - free.items.length);

    await client.call("POST", "/api/billing/checkout", { plan: "pro" });
    const pro = (await client.call("GET", "/api/ev?minEv=0")).json;
    expect(pro.locked).toBe(0);
    expect(pro.items.length).toBe(pro.total);
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

describe("sample feed pricing", () => {
  it("never lets one book's own prices form an arbitrage", () => {
    for (let seed = 1; seed <= 40; seed++) {
      const feed = new SampleFeed(seed, () => 0);
      for (let t = 0; t < 5; t++) {
        for (const market of feed.listMarkets()) {
          for (const prices of Object.values(market.prices)) {
            if (!prices) continue;
            const implied = prices.reduce((sum, p) => sum + 1 / americanToDecimal(p), 0);
            expect(implied).toBeGreaterThan(1);
          }
        }
        feed.tick();
      }
    }
  });
});
