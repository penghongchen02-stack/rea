/**
 * Joins one game across platforms. A game matches only on league, date and
 * both team codes (after the league's explicit code aliases); there is no
 * fuzzy name matching, because a wrong pairing would show a false arbitrage.
 */
import type { Platform, PlatformGame, PmGame, PmLeague } from "./types.js";

export interface LeagueConfig extends PmLeague {
  /** Polymarket team code → Kalshi team code, where the platforms differ. Kalshi codes are canonical. */
  polymarketAliases: Record<string, string>;
}

export const PM_LEAGUES: LeagueConfig[] = [
  { id: "nfl", name: "NFL", kalshiSeries: "KXNFLGAME", polymarketSeries: "12185", polymarketAliases: { JAX: "JAC", LA: "LAR" } },
  { id: "nba", name: "NBA", kalshiSeries: "KXNBAGAME", polymarketSeries: "10345", polymarketAliases: {} },
  {
    id: "nhl",
    name: "NHL",
    kalshiSeries: "KXNHLGAME",
    polymarketSeries: "10346",
    polymarketAliases: { LAK: "LA", LAS: "VGK", CAL: "CGY", MON: "MTL", UTAH: "UTA" },
  },
  { id: "cfb", name: "College Football", kalshiSeries: "KXNCAAFGAME", polymarketSeries: "12756", polymarketAliases: {} },
];

export function canonicalCode(league: LeagueConfig, platform: Platform, code: string): string {
  return platform === "polymarket" ? league.polymarketAliases[code] ?? code : code;
}

export function matchGames(league: LeagueConfig, games: readonly PlatformGame[]): PmGame[] {
  const byKey = new Map<string, PmGame>();
  for (const game of games) {
    const codes = game.teams.map((t) => canonicalCode(league, game.platform, t.code));
    const key = `${league.id}:${game.date}:${[...codes].sort().join("-")}`;
    let merged = byKey.get(key);
    if (!merged) {
      merged = {
        id: key,
        league: league.id,
        date: game.date,
        startTime: game.startTime,
        title: game.title,
        teams: game.teams.map((t, i) => ({ code: codes[i]!, name: t.name, quotes: {} })),
        platforms: [],
      };
      byKey.set(key, merged);
    }
    if (merged.platforms.includes(game.platform)) continue; // duplicate listing on one platform
    merged.platforms.push(game.platform);
    merged.startTime ??= game.startTime;
    // Polymarket titles and nicknames read better ("Magic vs. Celtics"); prefer them for display.
    if (game.platform === "polymarket") merged.title = game.title;
    game.teams.forEach((team, i) => {
      const target = merged!.teams.find((t) => t.code === codes[i]);
      if (!target) return;
      target.quotes[game.platform] = team.quote;
      if (game.platform === "polymarket") target.name = team.name;
    });
  }
  return [...byKey.values()].sort((a, b) => (a.startTime ?? a.date).localeCompare(b.startTime ?? b.date));
}
