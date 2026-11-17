/** Kalshi public market data: one event per game, one yes/no market per team. */
import { z } from "zod";
import type { PlatformGame, PmLeague } from "./types.js";

const BASE = "https://api.elections.kalshi.com/trade-api/v2";

const dollars = z
  .string()
  .nullish()
  .transform((v) => (v === null || v === undefined || v === "" ? null : Number(v)))
  .pipe(z.number().min(0).max(1).nullable());

const marketSchema = z.object({
  ticker: z.string(),
  yes_sub_title: z.string().nullish(),
  yes_bid_dollars: dollars,
  yes_ask_dollars: dollars,
  status: z.string(),
  expected_expiration_time: z.string().nullish(),
});

const eventSchema = z.object({
  event_ticker: z.string(),
  title: z.string(),
  markets: z.array(marketSchema).default([]),
});

const responseSchema = z.object({ events: z.array(eventSchema) });

const MONTHS: Record<string, string> = { JAN: "01", FEB: "02", MAR: "03", APR: "04", MAY: "05", JUN: "06", JUL: "07", AUG: "08", SEP: "09", OCT: "10", NOV: "11", DEC: "12" };

/** "KXNBAGAME-26NOV17NYKTOR" → "2026-11-17"; null when the ticker has no date. */
export function kalshiEventDate(eventTicker: string): string | null {
  const match = /-(\d{2})([A-Z]{3})(\d{2})/.exec(eventTicker);
  const month = match && MONTHS[match[2]!];
  return match && month ? `20${match[1]}-${month}-${match[3]}` : null;
}

/** Ticker suffix after the event ticker is the team code: "...-NYKTOR-NYK" → "NYK". */
function teamCode(eventTicker: string, marketTicker: string): string | null {
  return marketTicker.startsWith(`${eventTicker}-`) ? marketTicker.slice(eventTicker.length + 1) : null;
}

export function parseKalshiEvents(league: PmLeague, body: unknown): PlatformGame[] {
  const { events } = responseSchema.parse(body);
  const games: PlatformGame[] = [];
  for (const event of events) {
    const date = kalshiEventDate(event.event_ticker);
    const markets = event.markets.filter((m) => m.status === "active");
    // Game-winner events have exactly one market per team; skip ties and specials.
    if (!date || markets.length !== 2) continue;
    const teams = markets.map((m) => {
      const code = teamCode(event.event_ticker, m.ticker);
      return code
        ? { code, name: m.yes_sub_title ?? code, quote: { platform: "kalshi" as const, marketId: m.ticker, bid: m.yes_bid_dollars, ask: m.yes_ask_dollars, url: null } }
        : null;
    });
    if (!teams[0] || !teams[1]) continue;
    games.push({
      platform: "kalshi",
      league: league.id,
      date,
      startTime: null,
      title: event.title,
      teams: [teams[0], teams[1]],
    });
  }
  return games;
}

export async function fetchKalshi(league: PmLeague, signal: AbortSignal): Promise<PlatformGame[]> {
  const url = `${BASE}/events?series_ticker=${encodeURIComponent(league.kalshiSeries)}&status=open&with_nested_markets=true&limit=200`;
  const res = await fetch(url, { signal, headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`Kalshi returned HTTP ${res.status}`);
  return parseKalshiEvents(league, await res.json());
}
