import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type EvOpportunity, type Gated } from "../api";
import { Filters, LockedBanner, PageHeader, State, query, type Market } from "../components";
import { POLL_MS, fmtAmerican, fmtMoney, fmtPct, fmtTime, marketLabel, outcomeLabel } from "../format";
import { useSession } from "../session";

export function EvPage() {
  const { user, bookName } = useSession();
  const [sport, setSport] = useState("");
  const [market, setMarket] = useState<Market>("");
  const [minEv, setMinEv] = useState(1);
  const [bankroll, setBankroll] = useState(1000);
  const [kellyMultiplier, setKellyMultiplier] = useState(0.25);
  const [tracked, setTracked] = useState<Set<string>>(new Set());
  const { data, error, loading } = useApi<Gated<EvOpportunity>>(`/api/ev${query({ sport, market, minEv })}`, POLL_MS);

  async function track(op: EvOpportunity, stake: number) {
    await api("/api/bets", {
      method: "POST",
      body: {
        description: `${outcomeLabel(op.kind, op.point, op.outcome, op.outcomeIndex)} (${op.event.away} @ ${op.event.home})`,
        book: bookName(op.bookId),
        americanOdds: op.price,
        stake: Math.max(1, Math.round(stake)),
      },
    });
    setTracked((s) => new Set(s).add(op.id));
  }

  return (
    <>
      <PageHeader
        title="Positive EV Bets"
        subtitle="Prices that beat the sharp book's no-vig fair line. Stake suggestions use fractional Kelly."
        updatedAt={data?.updatedAt}
      />
      <Filters sport={sport} market={market} onSport={setSport} onMarket={setMarket}>
        <label>
          Min EV %
          <input type="number" step="0.5" value={minEv} onChange={(e) => setMinEv(Number(e.target.value))} />
        </label>
        <label>
          Bankroll
          <input type="number" min="0" value={bankroll} onChange={(e) => setBankroll(Number(e.target.value))} />
        </label>
        <label>
          Kelly
          <select value={kellyMultiplier} onChange={(e) => setKellyMultiplier(Number(e.target.value))}>
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
                            const stake = bankroll * op.kelly * kellyMultiplier;
                return (
                  <tr key={op.id}>
                    <td className="num"><span className="pill pill-green">{fmtPct(op.ev)}</span></td>
                    <td>
                      <div className="event-name">{op.event.away} @ {op.event.home}</div>
                      <div className="muted small">{fmtTime(op.event.startTime)} · {marketLabel(op.kind, op.point)}</div>
                    </td>
                    <td>{outcomeLabel(op.kind, op.point, op.outcome, op.outcomeIndex)}</td>
                    <td>{bookName(op.bookId)}</td>
                    <td className="num price">{fmtAmerican(op.price)}</td>
                    <td className="num muted">{fmtAmerican(op.fairPrice)}</td>
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
        <LockedBanner locked={data?.locked ?? 0} noun="+EV bet" />
      </State>
    </>
  );
}
