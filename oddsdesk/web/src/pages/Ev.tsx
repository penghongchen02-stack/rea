import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type EvMethod, type EvOpportunity, type Gated } from "../api";
import { EventCell, Filters, LockedBanner, PageHeader, State, useTool, type ToolFilters } from "../components";
import { fmtAmerican, fmtMoney, fmtPct, marketLabel, outcomeLabel } from "../format";
import { usePrefs } from "../prefs";
import { useSession } from "../session";

const COPY: Record<EvMethod, { title: string; subtitle: string }> = {
  market: {
    title: "+EV Bets",
    subtitle: "Prices better than the fair line made by removing the margin from every book's price and averaging them.",
  },
  sharp: {
    title: "Sharp +EV",
    subtitle: "Prices better than the fair line from the sharp book (Sharpline) alone, after removing its margin.",
  },
};

export function EvPage({ method }: { method: EvMethod }) {
  const { user, bookName } = useSession();
  const { prefs, update } = usePrefs();
  const [filters, setFilters] = useState<ToolFilters>({ sport: "", market: "", period: "" });
  const [minEv, setMinEv] = useState(1);
  const [tracked, setTracked] = useState<Set<string>>(new Set());
  const { data, error, loading, reload } = useTool<Gated<EvOpportunity>>("/api/ev", filters, { minEv, method });

  async function track(op: EvOpportunity, stake: number) {
    await api("/api/bets", {
      method: "POST",
      body: {
        description: `${outcomeLabel(op.market, op.outcomeIndex, op.point)} · ${marketLabel(op.market)} (${op.event.title})`,
        book: bookName(op.bookId),
        americanOdds: op.price,
        stake: Math.max(1, Math.round(stake)),
      },
    });
    setTracked((s) => new Set(s).add(op.id));
  }

  return (
    <>
      <PageHeader title={COPY[method].title} subtitle={COPY[method].subtitle} updatedAt={data?.updatedAt} onRefresh={() => void reload()} />
      <Filters value={filters} onChange={setFilters} markets={["", "moneyline", "spread", "total", "team_total", "player_prop", "future"]}>
        <label>
          Min EV %
          <input type="number" step="0.5" value={minEv} onChange={(e) => setMinEv(Number(e.target.value))} />
        </label>
        <label>
          Bankroll
          <input type="number" min="0" value={prefs.bankroll} onChange={(e) => update({ bankroll: Number(e.target.value) })} />
        </label>
        <label>
          Kelly
          <select value={prefs.kellyMultiplier} onChange={(e) => update({ kellyMultiplier: Number(e.target.value) })}>
            <option value={1}>Full</option>
            <option value={0.5}>Half</option>
            <option value={0.25}>Quarter</option>
            <option value={0.1}>Tenth</option>
          </select>
        </label>
      </Filters>
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">EV</th>
                <th>Event</th>
                <th>Bet</th>
                <th>Book</th>
                <th className="num">Price</th>
                <th className="num">Fair</th>
                <th className="num">Win prob</th>
                <th className="num">Stake</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data?.items.map((op) => {
                const stake = prefs.bankroll * op.kelly * prefs.kellyMultiplier;
                return (
                  <tr key={op.id}>
                    <td className="num"><span className="pill pill-green">{fmtPct(op.ev)}</span></td>
                    <td><EventCell title={op.event.title} startTime={op.event.startTime} detail={marketLabel(op.market)} /></td>
                    <td>{outcomeLabel(op.market, op.outcomeIndex, op.point)}</td>
                    <td>{bookName(op.bookId)}</td>
                    <td className="num price">{fmtAmerican(op.price)}</td>
                    <td className="num muted" title={`Fair line from ${op.fairSources} book${op.fairSources === 1 ? "" : "s"}`}>{fmtAmerican(op.fairPrice)}</td>
                    <td className="num">{fmtPct(op.fairProbability, 1)}</td>
                    <td className="num">{fmtMoney(stake)}</td>
                    <td>
                      {user ? (
                        <button className="btn btn-small" disabled={tracked.has(op.id)} onClick={() => void track(op, stake)}>
                          {tracked.has(op.id) ? "Tracked" : "Track"}
                        </button>
                      ) : (
                        <Link to="/login" className="btn btn-small btn-ghost">Log in to track</Link>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <LockedBanner data={data} noun="+EV bets" />
      </State>
    </>
  );
}
