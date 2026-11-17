/** Prediction-market game moneylines, normalised across platforms. Prices are dollars per $1 contract. */

export type Platform = "kalshi" | "polymarket";

export const PLATFORM_NAME: Record<Platform, string> = { kalshi: "Kalshi", polymarket: "Polymarket" };

export interface PmLeague {
  id: string;
  name: string;
  /** Kalshi series ticker for single-game winner markets. */
  kalshiSeries: string;
  /** Polymarket series id for the league's game events. */
  polymarketSeries: string;
}

/** Top of book for "this team wins" on one platform. Null sides mean no resting order. */
export interface PmQuote {
  platform: Platform;
  /** Platform's own market identifier (Kalshi ticker, Polymarket market slug). */
  marketId: string;
  bid: number | null;
  ask: number | null;
  url: string | null;
}

export interface PmTeam {
  /** Team code as both platforms use it, e.g. "NYK". */
  code: string;
  name: string;
}

/** One platform's view of a game. */
export interface PlatformGame {
  platform: Platform;
  league: string;
  /** Game date as the platform labels it (YYYY-MM-DD). */
  date: string;
  startTime: string | null;
  title: string;
  /** Exactly two teams, each with that platform's quote. */
  teams: [PmTeam & { quote: PmQuote }, PmTeam & { quote: PmQuote }];
}

/** A game with quotes from every platform listing it. */
export interface PmGame {
  id: string;
  league: string;
  date: string;
  startTime: string | null;
  title: string;
  teams: (PmTeam & { quotes: Partial<Record<Platform, PmQuote>> })[];
  platforms: Platform[];
}

export interface SourceStatus {
  platform: Platform;
  league: string;
  ok: boolean;
  games: number;
  error?: string;
  fetchedAt: string;
}
