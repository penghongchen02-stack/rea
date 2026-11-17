/** Polymarket public market data (Gamma API): one event per game with a two-outcome moneyline market. */
import { z } from "zod";
import type { PlatformGame, PmLeague } from "./types.js";

const BASE = "https://gamma-api.polymarket.com";

const price = z.number().min(0).max(1).nullish().transform((v) => v ?? null);

/** Gamma encodes some arrays as JSON strings, e.g. "[\"Spurs\", \"Kings\"]". */
const jsonStringArray = z.string().transform((s, ctx) => {
  try {
    const parsed = z.array(z.string()).parse(JSON.parse(s));
    return parsed;
  } catch {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: "outcomes is not a JSON string array" });
    return z.NEVER;
  }
});

const marketSchema = z.object({
  slug: z.string(),
  outcomes: jsonStringArray,
  bestBid: price,
  bestAsk: price,
  sportsMarketType: z.string().nullish(),
  acceptingOrders: z.boolean().nullish(),
  closed: z.boolean().nullish(),
});

const eventSchema = z.object({
  slug: z.string(),
  title: z.string(),
  eventDate: z.string().nullish(),
  startTime: z.string().nullish(),
  closed: z.boolean().nullish(),
  markets: z.array(z.unknown()).default([]),
});

/** "nba-orl-bos-2026-11-16" → ["ORL", "BOS"], in the same order as the moneyline outcomes. */
export function polymarketTeamCodes(slug: string): [string, string] | null {
  const match = /^[a-z0-9]+-([a-z0-9]+)-([a-z0-9]+)-\d{4}-\d{2}-\d{2}$/.exec(slug);
  return match ? [match[1]!.toUpperCase(), match[2]!.toUpperCase()] : null;
}

export function parsePolymarketEvents(league: PmLeague, body: unknown): PlatformGame[] {
  const events = z.array(eventSchema).parse(body);
  const games: PlatformGame[] = [];
  for (const event of events) {
    const codes = polymarketTeamCodes(event.slug);
    if (event.closed || !codes || !event.eventDate) continue;
    // Parse markets one by one so a single malformed prop market does not drop the game.
    const moneyline = event.markets
      .map((m) => marketSchema.safeParse(m))
      .flatMap((r) => (r.success ? [r.data] : []))
      .find((m) => m.sportsMarketType === "moneyline" && m.outcomes.length === 2);
    if (!moneyline || moneyline.closed || moneyline.acceptingOrders === false) continue;

    // Best bid/ask are for the first outcome; the second outcome is its complement.
    const url = `https://polymarket.com/event/${event.slug}`;
    const first = { platform: "polymarket" as const, marketId: moneyline.slug, bid: moneyline.bestBid, ask: moneyline.bestAsk, url };
    const second = {
      platform: "polymarket" as const,
      marketId: moneyline.slug,
      bid: moneyline.bestAsk === null ? null : round4(1 - moneyline.bestAsk),
      ask: moneyline.bestBid === null ? null : round4(1 - moneyline.bestBid),
      url,
    };
    games.push({
      platform: "polymarket",
      league: league.id,
      date: event.eventDate,
      startTime: event.startTime ?? null,
      title: event.title,
      teams: [
        { code: codes[0], name: moneyline.outcomes[0]!, quote: first },
        { code: codes[1], name: moneyline.outcomes[1]!, quote: second },
      ],
    });
  }
  return games;
}

export async function fetchPolymarket(league: PmLeague, signal: AbortSignal): Promise<PlatformGame[]> {
  // Nearest unfinished games first, so the page overlaps Kalshi's open game markets.
  const endMin = new Date(Date.now() - 6 * 3_600_000).toISOString();
  const url = `${BASE}/events?series_id=${encodeURIComponent(league.polymarketSeries)}&closed=false&limit=100&order=startTime&ascending=true&end_date_min=${endMin}`;
  // Pages above 50 events are only served compressed; fetch decompresses gzip transparently.
  const res = await fetch(url, { signal, headers: { accept: "application/json", "accept-encoding": "gzip" } });
  if (!res.ok) throw new Error(`Polymarket returned HTTP ${res.status}`);
  return parsePolymarketEvents(league, await res.json());
}

function round4(n: number): number {
  return Math.round(n * 10_000) / 10_000;
}
