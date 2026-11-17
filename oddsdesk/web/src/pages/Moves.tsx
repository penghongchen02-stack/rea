import { useState } from "react";
import type { Gated, LineMove } from "../api";
import { EventCell, Filters, LockedBanner, PageHeader, State, useTool, type ToolFilters } from "../components";
import { fmtAgo, fmtAmerican, marketLabel, outcomeLabel } from "../format";
import { useSession } from "../session";

export function MovesPage() {
  const { bookName } = useSession();
  const [filters, setFilters] = useState<ToolFilters>({ sport: "", market: "", period: "" });
  const [minMove, setMinMove] = useState(2);
  const { data, error, loading, reload } = useTool<Gated<LineMove>>("/api/moves", filters, { minMove });

  return (
    <>
      <PageHeader
        title="Moving Lines"
        subtitle="The biggest price changes since each book opened its line. A side that shortened at one book may still be available at the old price elsewhere."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <Filters value={filters} onChange={setFilters} markets={["", "moneyline", "spread", "total", "team_total", "player_prop", "future"]}>
        <label>
          Min move (pts)
          <input type="number" min="0" step="0.5" value={minMove} onChange={(e) => setMinMove(Number(e.target.value))} />
        </label>
      </Filters>
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Bet</th>
                <th>Book</th>
                <th className="num">Open</th>
                <th />
                <th className="num">Now</th>
                <th className="num">Move</th>
                <th>Changed</th>
                <th className="num">Best now</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((m) => {
                const shortened = m.impliedChange > 0;
                return (
                  <tr key={m.id}>
                    <td><EventCell title={m.event.title} startTime={m.event.startTime} detail={marketLabel(m.market)} /></td>
                    <td>{outcomeLabel(m.market, m.outcomeIndex, m.point)}</td>
                    <td>{bookName(m.bookId)}</td>
                    <td className="num muted">{fmtAmerican(m.from)}</td>
                    <td className={shortened ? "red" : "green"} aria-label={shortened ? "price shortened" : "price lengthened"}>{shortened ? "↘" : "↗"}</td>
                    <td className="num price">{fmtAmerican(m.to)}</td>
                    <td className={`num ${shortened ? "red" : "green"}`} title="Change in implied probability">
                      {shortened ? "+" : "−"}{Math.abs(m.impliedChange * 100).toFixed(1)} pts
                    </td>
                    <td className="muted small">{fmtAgo(m.changedAt)}</td>
                    <td className="num">
                      {m.best ? (
                        <>
                          <span className="price">{fmtAmerican(m.best.price)}</span>
                          <div className="muted small">{m.best.bookIds.map(bookName).join(", ")}</div>
                        </>
                      ) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <LockedBanner data={data} noun="line moves" />
      </State>
    </>
  );
}
