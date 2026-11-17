import { americanToDecimal, arbitrage, devig, expectedValue, kellyFraction } from "../shared/odds-math.js";
import type { Book, MarketKind, MarketLine, Offer, Period, SportEvent } from "./types.js";

export interface MarketFilter {
  sportId?: string;
  kind?: MarketKind;
  period?: Period;
  eventId?: string;
  propType?: string;
  /** Only quotes from these books; undefined means every book. */
  books?: readonly string[];
}

/** The parts of a market a result row needs to describe itself. */
export type MarketRef = Pick<MarketLine, "id" | "kind" | "period" | "participant" | "propType" | "outcomes">;

export interface BoardRow {
  market: MarketLine;
  event: SportEvent;
  /** Best American price per outcome at the main line, and the books offering it. */
  best: { price: number | null; bookIds: string[] }[];
}

export type EvMethod = "market" | "sharp";

export interface EvOpportunity {
  id: string;
  event: SportEvent;
  market: MarketRef;
  point: number | null;
  outcomeIndex: number;
  bookId: string;
  price: number;
  fairPrice: number;
  fairProbability: number;
  ev: number;
  kelly: number;
  method: EvMethod;
  /** Number of books whose no-vig prices formed the fair line. */
  fairSources: number;
}

export interface HoldLeg {
  outcomeIndex: number;
  bookId: string;
  price: number;
  /** Share of the total stake for equal payouts. */
  stakeShare: number;
}

export interface HoldRow {
  id: string;
  event: SportEvent;
  market: MarketRef;
  point: number | null;
  /** Combined margin of the best prices; negative means an arbitrage. */
  hold: number;
  legs: HoldLeg[];
}

export interface MiddleRow {
  id: string;
  event: SportEvent;
  market: MarketRef;
  /** Width of the window where both bets win. */
  gap: number;
  legs: { outcomeIndex: number; bookId: string; point: number; price: number; stakeShare: number }[];
  /** Profit in units of total stake when the result lands in the middle. */
  profitIfMiddle: number;
  /** Loss in units of total stake when only one side wins (negative means guaranteed profit). */
  lossOtherwise: number;
  /** Middle hit rate needed to break even. */
  breakEven: number;
}

export interface LineMove {
  id: string;
  event: SportEvent;
  market: MarketRef;
  bookId: string;
  outcomeIndex: number;
  /** The book's line for this market (unchanged between the two prices). */
  point: number | null;
  from: number;
  to: number;
  /** Change in implied probability of this outcome (positive: the price shortened). */
  impliedChange: number;
  changedAt: string;
  /** Best current price for this outcome at the main line across selected books. */
  best: { price: number; bookIds: string[] } | null;
}

/** Handicap or line as it applies to one outcome: spreads flip sign for the second side. */
export function outcomePoint(kind: MarketKind, point: number | null, outcomeIndex: number): number | null {
  if (point === null) return null;
  return kind === "spread" && outcomeIndex === 1 ? -point : point;
}

export function buildBoard(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter): BoardRow[] {
  const rows: BoardRow[] = [];
  eachMarket(events, markets, filter, (event, market) => {
    rows.push({ market, event, best: bestPrices(market, filter, market.point) });
  });
  return rows.sort((a, b) => a.event.startTime.localeCompare(b.event.startTime) || a.market.id.localeCompare(b.market.id));
}

/**
 * +EV against a fair line: either the sharp book's no-vig price ("sharp") or
 * the average no-vig price across every book quoting the same line ("market").
 */
export function findEv(
  events: readonly SportEvent[],
  markets: readonly MarketLine[],
  books: readonly Book[],
  filter: MarketFilter & { minEv: number; method: EvMethod },
): EvOpportunity[] {
  const sharpIds = new Set(books.filter((b) => b.sharp).map((b) => b.id));
  const results: EvOpportunity[] = [];
  eachMarket(events, markets, filter, (event, market) => {
    for (const group of groupByPoint(market, undefined)) {
      const fair = fairLine(group.quotes, sharpIds, filter.method);
      if (!fair) continue;
      for (const [bookId, prices] of group.quotes) {
        if (!allowed(filter, bookId) || (filter.method === "sharp" && sharpIds.has(bookId))) continue;
        prices.forEach((price, index) => {
          const p = fair.probs[index]!;
          const d = americanToDecimal(price);
          const ev = expectedValue(p, d);
          if (ev < filter.minEv) return;
          results.push({
            id: `${market.id}:${group.point}:${index}:${bookId}:${filter.method}`,
            event,
            market: ref(market),
            point: group.point,
            outcomeIndex: index,
            bookId,
            price,
            fairPrice: Math.round(fairAmerican(p)),
            fairProbability: p,
            ev,
            kelly: kellyFraction(p, d),
            method: filter.method,
            fairSources: fair.sources,
          });
        });
      }
    }
  });
  return results.sort((a, b) => b.ev - a.ev);
}

/** Best price per outcome on each line, ranked by combined hold (lowest first). */
export function findLowHold(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter): HoldRow[] {
  const rows: HoldRow[] = [];
  eachMarket(events, markets, filter, (event, market) => {
    for (const group of groupByPoint(market, filter)) {
      const best = market.outcomes.map((_, index) => bestOf(group.quotes, index));
      if (best.some((b) => b === null)) continue;
      const legs = best as { price: number; bookIds: string[] }[];
      const plan = arbitrage(legs.map((b) => americanToDecimal(b.price)), 1);
      rows.push({
        id: `${market.id}:${group.point}`,
        event,
        market: ref(market),
        point: group.point,
        hold: -plan.profitMargin / (1 + plan.profitMargin),
        legs: legs.map((b, index) => ({ outcomeIndex: index, bookId: b.bookIds[0]!, price: b.price, stakeShare: plan.stakes[index]! })),
      });
    }
  });
  return rows.sort((a, b) => a.hold - b.hold);
}

/** Lines where the best prices across books guarantee a profit. */
export function findArbs(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter): HoldRow[] {
  return findLowHold(events, markets, filter).filter((row) => row.hold < 0);
}

const MIDDLE_KINDS: ReadonlySet<MarketKind> = new Set(["spread", "total", "team_total", "player_prop"]);

/** Opposite sides at different lines from two books, leaving a window where both win. */
export function findMiddles(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter): MiddleRow[] {
  const best = new Map<string, MiddleRow>();
  eachMarket(events, markets, filter, (event, market) => {
    if (!MIDDLE_KINDS.has(market.kind)) return;
    const quotes = Object.entries(market.offers).filter(
      (entry): entry is [string, Offer & { point: number }] => entry[1] !== null && entry[1].point !== null && allowed(filter, entry[0]),
    );
    for (const [bookA, a] of quotes) {
      for (const [bookB, b] of quotes) {
        if (bookA === bookB) continue;
        // Spread: first side at +a and second side at -b both win when a > b. Totals: Over a and Under b win when b > a.
        const gap = market.kind === "spread" ? a.point - b.point : b.point - a.point;
        if (gap <= 0) continue;
        const d0 = americanToDecimal(a.prices[0]!);
        const d1 = americanToDecimal(b.prices[1]!);
        const payout = (d0 * d1) / (d0 + d1);
        const profitIfMiddle = 2 * payout - 1;
        const lossOtherwise = 1 - payout;
        const row: MiddleRow = {
          id: `${market.id}:${a.point}:${b.point}`,
          event,
          market: ref(market),
          gap,
          legs: [
            { outcomeIndex: 0, bookId: bookA, point: a.point, price: a.prices[0]!, stakeShare: d1 / (d0 + d1) },
            { outcomeIndex: 1, bookId: bookB, point: b.point, price: b.prices[1]!, stakeShare: d0 / (d0 + d1) },
          ],
          profitIfMiddle,
          lossOtherwise,
          breakEven: lossOtherwise <= 0 ? 0 : lossOtherwise / (profitIfMiddle + lossOtherwise),
        };
        const existing = best.get(row.id);
        if (!existing || row.breakEven < existing.breakEven) best.set(row.id, row);
      }
    }
  });
  return [...best.values()].sort((x, y) => x.breakEven - y.breakEven || y.gap - x.gap);
}

/** Each book's biggest price change on its line since opening, largest moves first. */
export function findMoves(events: readonly SportEvent[], markets: readonly MarketLine[], filter: MarketFilter & { minChange: number }): LineMove[] {
  const moves: LineMove[] = [];
  eachMarket(events, markets, filter, (event, market) => {
    const best = bestPrices(market, filter, market.point);
    for (const [bookId, now] of Object.entries(market.offers)) {
      const open = market.opening[bookId];
      // Price changes are only comparable on the same line.
      if (!now || !open || now.point !== open.point || !allowed(filter, bookId)) continue;
      let pick: LineMove | null = null;
      for (let index = 0; index < market.outcomes.length; index++) {
        const change = 1 / americanToDecimal(now.prices[index]!) - 1 / americanToDecimal(open.prices[index]!);
        if (Math.abs(change) < filter.minChange) continue;
        if (pick && Math.abs(change) <= Math.abs(pick.impliedChange)) continue;
        const b = best[index];
        pick = {
          id: `${market.id}:${bookId}`,
          event,
          market: ref(market),
          bookId,
          outcomeIndex: index,
          point: now.point,
          from: open.prices[index]!,
          to: now.prices[index]!,
          impliedChange: change,
          changedAt: market.changedAt[bookId] ?? "",
          best: b && b.price !== null ? { price: b.price, bookIds: b.bookIds } : null,
        };
      }
      if (pick) moves.push(pick);
    }
  });
  return moves.sort((a, b) => Math.abs(b.impliedChange) - Math.abs(a.impliedChange));
}

// --- helpers ------------------------------------------------------------------

interface PointGroup {
  point: number | null;
  quotes: [string, number[]][];
}

/** Quotes grouped by line, so only like-for-like prices are compared. */
function groupByPoint(market: MarketLine, filter: MarketFilter | undefined): PointGroup[] {
  const groups = new Map<number | null, PointGroup>();
  for (const [bookId, offer] of Object.entries(market.offers)) {
    if (!offer || (filter && !allowed(filter, bookId))) continue;
    const group = groups.get(offer.point) ?? { point: offer.point, quotes: [] };
    group.quotes.push([bookId, offer.prices]);
    groups.set(offer.point, group);
  }
  return [...groups.values()];
}

function fairLine(quotes: [string, number[]][], sharpIds: ReadonlySet<string>, method: EvMethod): { probs: number[]; sources: number } | null {
  if (method === "sharp") {
    const sharp = quotes.find(([bookId]) => sharpIds.has(bookId));
    return sharp ? { probs: devig(sharp[1].map(americanToDecimal)), sources: 1 } : null;
  }
  // A market average needs enough books to mean anything.
  if (quotes.length < 3) return null;
  const devigged = quotes.map(([, prices]) => devig(prices.map(americanToDecimal)));
  const probs = devigged[0]!.map((_, i) => devigged.reduce((sum, p) => sum + p[i]!, 0) / devigged.length);
  return { probs, sources: quotes.length };
}

function bestPrices(market: MarketLine, filter: MarketFilter, point: number | null): BoardRow["best"] {
  const quotes = Object.entries(market.offers)
    .filter(([bookId, offer]) => offer && offer.point === point && allowed(filter, bookId))
    .map(([bookId, offer]) => [bookId, offer!.prices] as [string, number[]]);
  return market.outcomes.map((_, index) => bestOf(quotes, index) ?? { price: null, bookIds: [] });
}

function bestOf(quotes: [string, number[]][], index: number): { price: number; bookIds: string[] } | null {
  let best: { price: number; decimal: number; bookIds: string[] } | null = null;
  for (const [bookId, prices] of quotes) {
    const price = prices[index];
    if (price === undefined) continue;
    const decimal = americanToDecimal(price);
    if (!best || decimal > best.decimal) best = { price, decimal, bookIds: [bookId] };
    else if (decimal === best.decimal) best.bookIds.push(bookId);
  }
  return best && { price: best.price, bookIds: best.bookIds };
}

function eachMarket(
  events: readonly SportEvent[],
  markets: readonly MarketLine[],
  filter: MarketFilter,
  visit: (event: SportEvent, market: MarketLine) => void,
): void {
  const byId = new Map(events.map((e) => [e.id, e]));
  for (const market of markets) {
    const event = byId.get(market.eventId);
    if (!event) continue;
    if (filter.sportId && event.sportId !== filter.sportId) continue;
    if (filter.eventId && event.id !== filter.eventId) continue;
    if (filter.kind && market.kind !== filter.kind) continue;
    if (filter.period && market.period !== filter.period) continue;
    if (filter.propType && market.propType !== filter.propType) continue;
    visit(event, market);
  }
}

function allowed(filter: MarketFilter, bookId: string): boolean {
  return !filter.books || filter.books.includes(bookId);
}

function ref(market: MarketLine): MarketRef {
  const { id, kind, period, participant, propType, outcomes } = market;
  return { id, kind, period, participant, propType, outcomes };
}

function fairAmerican(probability: number): number {
  const d = 1 / probability;
  return d >= 2 ? (d - 1) * 100 : -100 / (d - 1);
}
