import { americanToDecimal, arbitrage, devig, expectedValue, kellyFraction } from "../shared/odds-math.js";
import type { Book, MarketKind, MarketLine, SportEvent } from "./types.js";

export interface BoardRow {
  market: MarketLine;
  event: SportEvent;
  /** Best American price per outcome and the books offering it. */
  best: { price: number | null; bookIds: string[] }[];
}

export interface EvOpportunity {
  id: string;
  event: SportEvent;
  marketId: string;
  kind: MarketKind;
  point: number | null;
  outcome: string;
  outcomeIndex: number;
  bookId: string;
  price: number;
  fairPrice: number;
  fairProbability: number;
  ev: number;
  kelly: number;
}

export interface ArbOpportunity {
  id: string;
  event: SportEvent;
  marketId: string;
  kind: MarketKind;
  point: number | null;
  margin: number;
  legs: { outcome: string; bookId: string; price: number; stakeShare: number }[];
}

export interface MarketFilter {
  sportId?: string;
  kind?: MarketKind;
}

export function buildBoard(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter): BoardRow[] {
  const byId = indexEvents(events);
  const rows: BoardRow[] = [];
  for (const market of markets) {
    const event = byId.get(market.eventId);
    if (!event || !matches(event, market, filter)) continue;
    const best = market.outcomes.map((_, index) => {
      let price: number | null = null;
      let bestDecimal = -Infinity;
      let bookIds: string[] = [];
      for (const [bookId, prices] of Object.entries(market.prices)) {
        const p = prices?.[index];
        if (p === undefined) continue;
        const d = americanToDecimal(p);
        if (d > bestDecimal) {
          price = p;
          bestDecimal = d;
          bookIds = [bookId];
        } else if (d === bestDecimal) {
          bookIds.push(bookId);
        }
      }
      return { price, bookIds };
    });
    rows.push({ market, event, best });
  }
  return rows.sort((a, b) => a.event.startTime.localeCompare(b.event.startTime) || a.market.id.localeCompare(b.market.id));
}

/** Price every non-sharp offer against the sharp book's no-vig line. */
export function findEv(
  events: readonly SportEvent[],
  markets: readonly MarketLine[],
  books: readonly Book[],
  filter: MarketFilter & { minEv: number },
): EvOpportunity[] {
  const byId = indexEvents(events);
  const sharp = books.find((b) => b.sharp);
  if (!sharp) return [];
  const results: EvOpportunity[] = [];
  for (const market of markets) {
    const event = byId.get(market.eventId);
    const reference = market.prices[sharp.id];
    if (!event || !reference || !matches(event, market, filter)) continue;
    const fair = devig(reference.map(americanToDecimal));
    for (const book of books) {
      if (book.sharp) continue;
      const prices = market.prices[book.id];
      if (!prices) continue;
      prices.forEach((price, index) => {
        const p = fair[index]!;
        const d = americanToDecimal(price);
        const ev = expectedValue(p, d);
        if (ev < filter.minEv) return;
        results.push({
          id: `${market.id}:${index}:${book.id}`,
          event,
          marketId: market.id,
          kind: market.kind,
          point: market.point,
          outcome: market.outcomes[index]!,
          outcomeIndex: index,
          bookId: book.id,
          price,
          fairPrice: Math.round(fairAmerican(p)),
          fairProbability: p,
          ev,
          kelly: kellyFraction(p, d),
        });
      });
    }
  }
  return results.sort((a, b) => b.ev - a.ev);
}

/** Best price per outcome across books; an arbitrage exists when implied probabilities sum below 1. */
export function findArbs(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter): ArbOpportunity[] {
  const rows = buildBoard(events, markets, filter);
  const results: ArbOpportunity[] = [];
  for (const row of rows) {
    const prices = row.best.map((b) => b.price);
    if (!prices.every((p): p is number => p !== null)) continue;
    const decimals = prices.map(americanToDecimal);
    const plan = arbitrage(decimals, 1);
    if (!plan.isArbitrage) continue;
    results.push({
      id: row.market.id,
      event: row.event,
      marketId: row.market.id,
      kind: row.market.kind,
      point: row.market.point,
      margin: plan.profitMargin,
      legs: row.best.map((b, index) => ({
        outcome: row.market.outcomes[index]!,
        bookId: b.bookIds[0]!,
        price: prices[index]!,
        stakeShare: plan.stakes[index]!,
      })),
    });
  }
  return results.sort((a, b) => b.margin - a.margin);
}

function fairAmerican(probability: number): number {
  const d = 1 / probability;
  return d >= 2 ? (d - 1) * 100 : -100 / (d - 1);
}

function indexEvents(events: readonly SportEvent[]): Map<string, SportEvent> {
  return new Map(events.map((e) => [e.id, e]));
}

function matches(event: SportEvent, market: MarketLine, filter: MarketFilter): boolean {
  return (!filter.sportId || event.sportId === filter.sportId) && (!filter.kind || market.kind === filter.kind);
}
