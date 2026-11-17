import { describe, expect, it } from "vitest";
import { findPmArbs, takerFee, DEFAULT_FEES } from "../server/pm/arbs.js";
import { kalshiEventDate, parseKalshiEvents } from "../server/pm/kalshi.js";
import { PM_LEAGUES, matchGames } from "../server/pm/match.js";
import { parsePolymarketEvents, polymarketTeamCodes } from "../server/pm/polymarket.js";
import { PmService, TIE_LEAGUES, type Fetcher } from "../server/pm/service.js";

const nfl = PM_LEAGUES.find((l) => l.id === "nfl")!;
const nba = PM_LEAGUES.find((l) => l.id === "nba")!;

/** Shapes trimmed from real public responses. */
const kalshiBody = {
  events: [
    {
      event_ticker: "KXNFLGAME-26NOV22JACNYG",
      title: "Jacksonville at New York G",
      markets: [
        { ticker: "KXNFLGAME-26NOV22JACNYG-JAC", yes_sub_title: "Jacksonville", yes_bid_dollars: "0.4400", yes_ask_dollars: "0.4500", status: "active" },
        { ticker: "KXNFLGAME-26NOV22JACNYG-NYG", yes_sub_title: "New York G", yes_bid_dollars: "0.5400", yes_ask_dollars: "0.5600", status: "active" },
      ],
    },
    // Three-market events (ties) and settled markets are not two-team winner markets.
    {
      event_ticker: "KXNFLGAME-26NOV23AAABBB",
      title: "Other",
      markets: [{ ticker: "KXNFLGAME-26NOV23AAABBB-AAA", yes_bid_dollars: "0.5", yes_ask_dollars: "0.51", status: "finalized" }],
    },
  ],
};

const polymarketBody = [
  {
    slug: "nfl-jax-nyg-2026-11-22",
    title: "Jaguars vs. Giants",
    eventDate: "2026-11-22",
    startTime: "2026-11-22T18:00:00Z",
    closed: false,
    markets: [
      { slug: "nfl-jax-nyg-2026-11-22", outcomes: "[\"Jaguars\", \"Giants\"]", bestBid: 0.47, bestAsk: 0.48, sportsMarketType: "moneyline", acceptingOrders: true, closed: false },
      { slug: "nfl-jax-nyg-2026-11-22-total-41pt5", outcomes: "[\"Over\", \"Under\"]", bestBid: 0.5, bestAsk: 0.51, sportsMarketType: "totals", acceptingOrders: true, closed: false },
      { slug: "broken", outcomes: "not json" },
    ],
  },
  { slug: "nfl-ari-kc-2026-11-22", title: "Cardinals vs. Chiefs", eventDate: "2026-11-22", closed: false, markets: [] },
];

describe("platform parsers", () => {
  it("reads Kalshi game dates and team codes from tickers", () => {
    expect(kalshiEventDate("KXNBAGAME-26NOV17NYKTOR")).toBe("2026-11-17");
    expect(kalshiEventDate("KXNBAGAME-SPECIAL")).toBeNull();
    const games = parseKalshiEvents(nfl, kalshiBody);
    expect(games).toHaveLength(1);
    expect(games[0]!.teams.map((t) => [t.code, t.quote.bid, t.quote.ask])).toEqual([
      ["JAC", 0.44, 0.45],
      ["NYG", 0.54, 0.56],
    ]);
  });

  it("reads the Polymarket moneyline and derives the second team's quote", () => {
    expect(polymarketTeamCodes("nba-orl-bos-2026-11-16")).toEqual(["ORL", "BOS"]);
    expect(polymarketTeamCodes("nba-season-winner")).toBeNull();
    const games = parsePolymarketEvents(nfl, polymarketBody);
    expect(games).toHaveLength(1); // the event without a moneyline is skipped
    const [jax, nyg] = games[0]!.teams;
    expect([jax.code, jax.quote.bid, jax.quote.ask]).toEqual(["JAX", 0.47, 0.48]);
    expect([nyg.code, nyg.quote.bid, nyg.quote.ask]).toEqual(["NYG", 0.52, 0.53]);
    expect(nyg.quote.url).toBe("https://polymarket.com/event/nfl-jax-nyg-2026-11-22");
  });

  it("rejects malformed responses instead of guessing", () => {
    expect(() => parseKalshiEvents(nfl, { events: [{ event_ticker: 3 }] })).toThrow();
    expect(() => parsePolymarketEvents(nfl, { error: "gzip_required" })).toThrow();
  });
});

describe("matching", () => {
  it("joins platforms on date and aliased team codes only", () => {
    const games = matchGames(nfl, [...parseKalshiEvents(nfl, kalshiBody), ...parsePolymarketEvents(nfl, polymarketBody)]);
    expect(games).toHaveLength(1);
    const [game] = games;
    expect(game!.platforms.sort()).toEqual(["kalshi", "polymarket"]);
    expect(game!.title).toBe("Jaguars vs. Giants");
    const jac = game!.teams.find((t) => t.code === "JAC")!;
    expect(jac.quotes.kalshi?.ask).toBe(0.45);
    expect(jac.quotes.polymarket?.ask).toBe(0.48);
  });

  it("keeps games apart when the date or a team differs", () => {
    const shifted = parsePolymarketEvents(nba, [{ ...polymarketBody[0], slug: "nba-jax-nyg-2026-11-23", eventDate: "2026-11-23" }]);
    const games = matchGames(nba, [...parseKalshiEvents(nba, kalshiBody), ...shifted]);
    expect(games).toHaveLength(2);
    expect(games.every((g) => g.platforms.length === 1)).toBe(true);
  });
});

describe("arbitrage", () => {
  const games = matchGames(nfl, [...parseKalshiEvents(nfl, kalshiBody), ...parsePolymarketEvents(nfl, polymarketBody)]);

  it("pairs the cheapest cross-platform asks and charges fees", () => {
    const [arb] = findPmArbs(games, DEFAULT_FEES, -1, TIE_LEAGUES);
    // Kalshi JAC 0.45 + Polymarket NYG 0.53 beats Polymarket JAX 0.48 + Kalshi NYG 0.56.
    expect(arb!.legs.map((l) => [l.teamCode, l.quote.platform, l.quote.ask])).toEqual([
      ["JAC", "kalshi", 0.45],
      ["NYG", "polymarket", 0.53],
    ]);
    const expectedCost = 0.45 + takerFee(DEFAULT_FEES, "kalshi", 0.45) + 0.53;
    expect(arb!.cost).toBeCloseTo(expectedCost);
    expect(arb!.edge).toBeCloseTo(1 - expectedCost);
    expect(arb!.tieRisk).toBe(true);
  });

  it("filters by minimum edge", () => {
    expect(findPmArbs(games, DEFAULT_FEES, 0.01, TIE_LEAGUES)).toEqual([]);
    expect(findPmArbs(games, { rate: { kalshi: 0, polymarket: 0 } }, 0.01, TIE_LEAGUES)).toHaveLength(1);
  });
});

describe("service", () => {
  it("serves the other platform when one fails, and retries the failure next time", async () => {
    let kalshiCalls = 0;
    const failing: Fetcher = async () => {
      kalshiCalls++;
      throw new Error("Kalshi returned HTTP 503");
    };
    const poly: Fetcher = async (league) => parsePolymarketEvents(league, polymarketBody);
    const service = new PmService({ kalshi: failing, polymarket: poly });
    const first = await service.snapshot("nfl");
    expect(first.games).toHaveLength(1);
    expect(first.sources.find((s) => s.platform === "kalshi")).toMatchObject({ ok: false, error: "Kalshi returned HTTP 503" });
    await service.snapshot("nfl");
    expect(kalshiCalls).toBe(2);
  });

  it("caches complete snapshots and shares concurrent fetches", async () => {
    let calls = 0;
    const counted: Fetcher = async (league) => {
      calls++;
      return parseKalshiEvents(league, kalshiBody);
    };
    const service = new PmService({ kalshi: counted, polymarket: async () => [] });
    await Promise.all([service.snapshot("nfl"), service.snapshot("nfl")]);
    await service.snapshot("nfl");
    expect(calls).toBe(1);
    await expect(service.snapshot("cricket")).rejects.toThrow(RangeError);
  });
});
