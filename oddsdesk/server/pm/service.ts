/**
 * Live prediction-market data with a short shared cache. Each league is
 * fetched from both platforms on demand when its cache is stale; concurrent
 * requests share one fetch. A failing platform is reported, not hidden, and
 * the other platform's games are still served.
 */
import { fetchKalshi } from "./kalshi.js";
import { PM_LEAGUES, matchGames, type LeagueConfig } from "./match.js";
import { fetchPolymarket } from "./polymarket.js";
import type { Platform, PlatformGame, PmGame, PmLeague, SourceStatus } from "./types.js";

export type Fetcher = (league: PmLeague, signal: AbortSignal) => Promise<PlatformGame[]>;

export interface PmSnapshot {
  league: string;
  fetchedAt: string;
  sources: SourceStatus[];
  games: PmGame[];
}

const FETCHERS: Record<Platform, Fetcher> = { kalshi: fetchKalshi, polymarket: fetchPolymarket };

/** Leagues where a regulation tie is possible, so a both-sides position can lose. */
export const TIE_LEAGUES: ReadonlySet<string> = new Set(["nfl"]);

export class PmService {
  private readonly cache = new Map<string, { at: number; snapshot: PmSnapshot }>();
  private readonly inflight = new Map<string, Promise<PmSnapshot>>();

  constructor(
    private readonly fetchers: Record<Platform, Fetcher> = FETCHERS,
    private readonly ttlMs = 30_000,
    private readonly timeoutMs = 15_000,
    private readonly now: () => number = Date.now,
  ) {}

  leagues(): Pick<LeagueConfig, "id" | "name">[] {
    return PM_LEAGUES.map(({ id, name }) => ({ id, name }));
  }

  async snapshot(leagueId: string): Promise<PmSnapshot> {
    const league = PM_LEAGUES.find((l) => l.id === leagueId);
    if (!league) throw new RangeError(`Unknown league "${leagueId}"`);
    const cached = this.cache.get(league.id);
    if (cached && this.now() - cached.at < this.ttlMs) return cached.snapshot;
    const running = this.inflight.get(league.id);
    if (running) return running;
    const task = this.load(league).finally(() => this.inflight.delete(league.id));
    this.inflight.set(league.id, task);
    return task;
  }

  private async load(league: LeagueConfig): Promise<PmSnapshot> {
    const fetchedAt = new Date(this.now()).toISOString();
    const signal = AbortSignal.timeout(this.timeoutMs);
    const platforms = Object.keys(this.fetchers) as Platform[];
    const settled = await Promise.allSettled(platforms.map((p) => this.fetchers[p](league, signal)));
    const sources: SourceStatus[] = [];
    const games: PlatformGame[] = [];
    settled.forEach((result, i) => {
      const platform = platforms[i]!;
      if (result.status === "fulfilled") {
        games.push(...result.value);
        sources.push({ platform, league: league.id, ok: true, games: result.value.length, fetchedAt });
      } else {
        sources.push({ platform, league: league.id, ok: false, games: 0, error: describe(result.reason), fetchedAt });
      }
    });
    const snapshot: PmSnapshot = { league: league.id, fetchedAt, sources, games: matchGames(league, games) };
    // Only cache when every platform answered, so a transient failure is retried on the next request.
    if (sources.every((s) => s.ok)) this.cache.set(league.id, { at: this.now(), snapshot });
    return snapshot;
  }
}

function describe(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "TimeoutError" || error.name === "AbortError") return "Timed out";
    if (error.name === "ZodError") return "Unexpected response format";
    const cause = (error as Error & { cause?: { code?: string } }).cause?.code;
    return cause ? `${error.message} (${cause})` : error.message;
  }
  return "Unknown error";
}
