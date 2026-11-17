/**
 * Cross-platform arbitrage on two-team game markets: buy "A wins" and
 * "B wins" so exactly one contract pays $1. Prices are top-of-book asks;
 * order-book depth is not checked.
 */
import type { Platform, PmGame, PmQuote } from "./types.js";

export interface FeeModel {
  /** Taker fee per contract = rate × price × (1 − price), per platform. */
  rate: Record<Platform, number>;
}

/** Kalshi's published taker fee coefficient; Polymarket defaults to no fee. Both are caller-adjustable. */
export const DEFAULT_FEES: FeeModel = { rate: { kalshi: 0.07, polymarket: 0 } };

export function takerFee(fees: FeeModel, platform: Platform, price: number): number {
  return fees.rate[platform] * price * (1 - price);
}

export interface PmArbLeg {
  team: string;
  teamCode: string;
  quote: PmQuote & { ask: number };
  fee: number;
}

export interface PmArb {
  id: string;
  game: Pick<PmGame, "id" | "league" | "date" | "startTime" | "title">;
  legs: [PmArbLeg, PmArbLeg];
  /** Cost of one contract on each side, fees included; the pair pays $1. */
  cost: number;
  /** Profit per $1 payout (1 − cost). Negative values are near misses. */
  edge: number;
  /** Profit as a share of money spent. */
  returnOnCost: number;
  /** A tie would settle both contracts as losers on at least one platform's rules. */
  tieRisk: boolean;
}

/** Best pair of asks across platforms for every matched game, best edge first. */
export function findPmArbs(games: readonly PmGame[], fees: FeeModel, minEdge: number, tieLeagues: ReadonlySet<string>): PmArb[] {
  const results: PmArb[] = [];
  for (const game of games) {
    if (game.teams.length !== 2) continue;
    const [a, b] = game.teams as [PmGame["teams"][number], PmGame["teams"][number]];
    let best: PmArb | null = null;
    for (const qa of Object.values(a.quotes)) {
      for (const qb of Object.values(b.quotes)) {
        // Same-platform pairs are the platform's own spread; only cross-platform pairs are arbitrage candidates.
        if (!qa || !qb || qa.platform === qb.platform || qa.ask === null || qb.ask === null) continue;
        const legA: PmArbLeg = { team: a.name, teamCode: a.code, quote: { ...qa, ask: qa.ask }, fee: takerFee(fees, qa.platform, qa.ask) };
        const legB: PmArbLeg = { team: b.name, teamCode: b.code, quote: { ...qb, ask: qb.ask }, fee: takerFee(fees, qb.platform, qb.ask) };
        const cost = legA.quote.ask + legA.fee + legB.quote.ask + legB.fee;
        const edge = 1 - cost;
        if (best && edge <= best.edge) continue;
        best = {
          id: `${game.id}:${qa.platform}-${qb.platform}`,
          game: { id: game.id, league: game.league, date: game.date, startTime: game.startTime, title: game.title },
          legs: [legA, legB],
          cost,
          edge,
          returnOnCost: edge / cost,
          tieRisk: tieLeagues.has(game.league),
        };
      }
    }
    if (best && best.edge >= minEdge) results.push(best);
  }
  return results.sort((x, y) => y.edge - x.edge);
}
