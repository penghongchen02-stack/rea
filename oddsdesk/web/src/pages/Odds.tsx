import { useState } from "react";
import { useApi, type BoardRow } from "../api";
import { Filters, PageHeader, State, query, type Market } from "../components";
import { POLL_MS, fmtAmerican, fmtTime, marketLabel, outcomeLabel } from "../format";
import { useSession } from "../session";

export function OddsPage() {
  const { meta } = useSession();
  const [sport, setSport] = useState("");
  const [market, setMarket] = useState<Market>("moneyline");
  const { data, error, loading } = useApi<{ updatedAt: string; rows: BoardRow[] }>(`/api/odds${query({ sport, market })}`, POLL_MS);
  const books = meta?.books ?? [];

  return (
    <>
      <PageHeader title="Odds Screen" subtitle="Compare every sportsbook's price side by side. Best price per outcome is highlighted." updatedAt={data?.updatedAt} />
      <Filters sport={sport} market={market} onSport={setSport} onMarket={setMarket} />
      <State loading={loading} error={error} empty={!data?.rows.length}>
        <div className="table-wrap">
          <table className="odds-table">
            <thead>
              <tr>
                <th>Event</th>
                <th>Outcome</th>
                {books.map((b) => (
                  <th key={b.id} className="num">
                    {b.name}
                    {b.sharp && <span className="tag">sharp</span>}
                  </th>
                ))}
                <th className="num">Best</th>
              </tr>
            </thead>
            <tbody>
              {data?.rows.map((row) =>
                row.market.outcomes.map((outcome, i) => (
                  <tr key={`${row.market.id}:${i}`} className={i === 0 ? "group-start" : ""}>
                    {i === 0 && (
                      <td rowSpan={row.market.outcomes.length} className="event-cell">
                        <div className="event-name">{row.event.away} @ {row.event.home}</div>
                        <div className="muted small">
                          {fmtTime(row.event.startTime)} · {marketLabel(row.market.kind, row.market.point)}
                        </div>
                      </td>
                    )}
                    <td>{outcomeLabel(row.market.kind, row.market.point, outcome, i)}</td>
                    {books.map((b) => {
                      const price = row.market.prices[b.id]?.[i];
                      const best = row.best[i]?.bookIds.includes(b.id);
                      return (
                        <td key={b.id} className={`num price ${best ? "best" : ""}`}>
                          {fmtAmerican(price)}
                        </td>
                      );
                    })}
                    <td className="num price best-col">{fmtAmerican(row.best[i]?.price)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      </State>
    </>
  );
}
