import { useState } from "react";
import { useApi, type Gated } from "../api";
import { LockedBanner, PageHeader, State, query } from "../components";
import { fmtAgo } from "../format";
import { usePrefs } from "../prefs";
import type { PmArb } from "../../../server/pm/arbs";
import type { PmGame, PmQuote, SourceStatus } from "../../../server/pm/types";
import { PLATFORM_NAME } from "../../../server/pm/types";

const LEAGUES = [
  { id: "nba", name: "NBA" },
  { id: "nfl", name: "NFL" },
  { id: "nhl", name: "NHL" },
  { id: "cfb", name: "College Football" },
];

const PM_POLL_MS = 30_000;

function cents(price: number | null | undefined): string {
  return price === null || price === undefined ? "—" : `${(price * 100).toFixed(1).replace(/\.0$/, "")}¢`;
}

function LeaguePicker({ value, onChange }: { value: string; onChange(v: string): void }) {
  return (
    <label>
      League
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {LEAGUES.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
    </label>
  );
}

function Sources({ sources }: { sources: SourceStatus[] }) {
  return (
    <div className="sources">
      {sources.map((s) => (
        <span key={s.platform} className={`badge ${s.ok ? "" : "badge-error"}`} title={s.error}>
          {PLATFORM_NAME[s.platform]}: {s.ok ? `${s.games} games` : `unavailable (${s.error})`}
        </span>
      ))}
    </div>
  );
}

function Quote({ quote, best }: { quote: PmQuote | undefined; best: boolean }) {
  if (!quote) return <td className="num muted">not listed</td>;
  return (
    <td className={`num ${best ? "best" : ""}`}>
      <span className="muted small">{cents(quote.bid)} / </span>
      <span className="price">{cents(quote.ask)}</span>
    </td>
  );
}

export function PmOddsPage() {
  const { prefs } = usePrefs();
  const [league, setLeague] = useState("nba");
  const [onlyBoth, setOnlyBoth] = useState(true);
  const { data, error, loading, reload } = useApi<{ fetchedAt: string; sources: SourceStatus[]; games: PmGame[] }>(
    `/api/pm/games${query({ league })}`,
    prefs.autoRefresh ? PM_POLL_MS : undefined,
  );
  const games = (data?.games ?? []).filter((g) => !onlyBoth || g.platforms.length > 1);

  return (
    <>
      <PageHeader
        title="Prediction Market Odds"
        subtitle="Live game-winner prices from Kalshi and Polymarket. Each cell shows bid / ask in cents per $1 contract; the cheaper ask is highlighted."
        updatedAt={data?.fetchedAt}
        onRefresh={() => void reload()}
      />
      <div className="filters">
        <LeaguePicker value={league} onChange={setLeague} />
        <label className="switch">
          <input type="checkbox" checked={onlyBoth} onChange={(e) => setOnlyBoth(e.target.checked)} />
          Only games on both platforms
        </label>
        {data && <Sources sources={data.sources} />}
      </div>
      <State loading={loading} error={error} empty={!games.length}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Game</th>
                <th>Team</th>
                <th className="num">Kalshi bid / ask</th>
                <th className="num">Polymarket bid / ask</th>
                <th className="num">Gap</th>
              </tr>
            </thead>
            <tbody>
              {games.map((game) =>
                game.teams.map((team, i) => {
                  const k = team.quotes.kalshi?.ask ?? null;
                  const p = team.quotes.polymarket?.ask ?? null;
                  const both = k !== null && p !== null;
                  return (
                    <tr key={`${game.id}:${team.code}`} className={i === 0 ? "group-start" : ""}>
                      {i === 0 && (
                        <td rowSpan={game.teams.length} className="event-cell">
                          <div className="event-name">{game.title}</div>
                          <div className="muted small">
                            {game.startTime ? new Date(game.startTime).toLocaleString(undefined, { weekday: "short", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" }) : game.date}
                          </div>
                        </td>
                      )}
                      <td>{team.name} <span className="muted small">{team.code}</span></td>
                      <Quote quote={team.quotes.kalshi} best={both && k! < p!} />
                      <Quote quote={team.quotes.polymarket} best={both && p! < k!} />
                      <td className="num muted">{both ? `${(Math.abs(k! - p!) * 100).toFixed(1)}¢` : "—"}</td>
                    </tr>
                  );
                }),
              )}
            </tbody>
          </table>
        </div>
      </State>
    </>
  );
}

export function PmArbsPage() {
  const { prefs } = usePrefs();
  const [league, setLeague] = useState("nba");
  const [kalshiFee, setKalshiFee] = useState(7);
  const [polymarketFee, setPolymarketFee] = useState(0);
  const [minEdge, setMinEdge] = useState(-2);
  const [payout, setPayout] = useState(100);
  const { data, error, loading, reload } = useApi<Gated<PmArb> & { fetchedAt: string; sources: SourceStatus[] }>(
    `/api/pm/arbs${query({ league, kalshiFee, polymarketFee, minEdge })}`,
    prefs.autoRefresh ? PM_POLL_MS : undefined,
  );

  return (
    <>
      <PageHeader
        title="Prediction Market Arbitrage"
        subtitle="Buy one team on Kalshi and the other on Polymarket so one contract pays $1 whatever happens. Prices are the current best asks; check depth before relying on size."
        updatedAt={data?.fetchedAt}
        onRefresh={() => void reload()}
      />
      <div className="filters">
        <LeaguePicker value={league} onChange={setLeague} />
        <label>
          Kalshi fee coeff. %
          <input type="number" min="0" step="0.5" value={kalshiFee} onChange={(e) => setKalshiFee(Number(e.target.value))} />
        </label>
        <label>
          Polymarket fee coeff. %
          <input type="number" min="0" step="0.5" value={polymarketFee} onChange={(e) => setPolymarketFee(Number(e.target.value))} />
        </label>
        <label>
          Min edge %
          <input type="number" step="0.5" value={minEdge} onChange={(e) => setMinEdge(Number(e.target.value))} />
        </label>
        <label>
          Payout target $
          <input type="number" min="1" value={payout} onChange={(e) => setPayout(Number(e.target.value))} />
        </label>
        {data && <Sources sources={data.sources} />}
      </div>
      <p className="muted small note">
        Fees are modelled as coefficient × price × (1 − price) per contract. Defaults: Kalshi 7%, Polymarket 0%; check each platform's current fee schedule.
        Negative edges are near misses, shown so you can watch them close.
      </p>
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Game</th>
                <th>Buy</th>
                <th className="num">Contracts</th>
                <th className="num">Cost</th>
                <th className="num">Profit</th>
                <th className="num">Edge</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((arb) => {
                const contracts = payout;
                const profit = arb.edge * contracts;
                return (
                  <tr key={arb.id}>
                    <td>
                      <div className="event-name">{arb.game.title}</div>
                      <div className="muted small">
                        {arb.game.startTime ? fmtAgoOrTime(arb.game.startTime) : arb.game.date}
                        {arb.tieRisk && <span className="tag inline warn"> tie risk</span>}
                      </div>
                    </td>
                    <td>
                      <div className="legs">
                        {arb.legs.map((leg) => (
                          <div key={leg.teamCode} className="leg">
                            <span>{leg.team} wins</span>
                            <span className="muted">
                              {leg.quote.url ? <a href={leg.quote.url} target="_blank" rel="noreferrer">{PLATFORM_NAME[leg.quote.platform]}</a> : PLATFORM_NAME[leg.quote.platform]}
                            </span>
                            <span className="price">{cents(leg.quote.ask)}</span>
                          </div>
                        ))}
                      </div>
                    </td>
                    <td className="num">{contracts} each</td>
                    <td className="num">${(arb.cost * contracts).toFixed(2)}</td>
                    <td className={`num ${profit >= 0 ? "green" : "red"}`}>{profit >= 0 ? "+" : "−"}${Math.abs(profit).toFixed(2)}</td>
                    <td className="num"><span className={`pill ${arb.edge > 0 ? "pill-green" : "pill-muted"}`}>{(arb.edge * 100).toFixed(2)}%</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <LockedBanner data={data} noun="prediction-market pairs" />
      </State>
    </>
  );
}

function fmtAgoOrTime(iso: string): string {
  const t = Date.parse(iso);
  if (t < Date.now()) return `started ${fmtAgo(iso)}`;
  return new Date(iso).toLocaleString(undefined, { weekday: "short", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });
}
