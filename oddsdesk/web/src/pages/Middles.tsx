import { useState } from "react";
import type { Gated, MiddleRow } from "../api";
import { EventCell, Filters, LockedBanner, PageHeader, State, useTool, type ToolFilters } from "../components";
import { fmtAmerican, fmtPct, fmtUnits, marketLabel, outcomeLabel } from "../format";
import { useSession } from "../session";

export function MiddlesPage() {
  const { bookName } = useSession();
  const [filters, setFilters] = useState<ToolFilters>({ sport: "", market: "", period: "" });
  const { data, error, loading, reload } = useTool<Gated<MiddleRow>>("/api/middles", filters);

  return (
    <>
      <PageHeader
        title="Middles"
        subtitle="Opposite sides at different lines from two books. If the result lands in the gap, both bets win; otherwise you lose only part of the margin. Stakes are split for equal payouts across 1 unit."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <Filters value={filters} onChange={setFilters} markets={["", "spread", "total", "team_total", "player_prop"]} />
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Event</th>
                <th>Bets</th>
                <th className="num">Gap</th>
                <th className="num">Middle hits</th>
                <th className="num">Otherwise</th>
                <th className="num">Break-even</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((row) => (
                <tr key={row.id}>
                  <td><EventCell title={row.event.title} startTime={row.event.startTime} detail={marketLabel(row.market)} /></td>
                  <td>
                    <div className="legs">
                      {row.legs.map((leg) => (
                        <div key={leg.outcomeIndex} className="leg">
                          <span>{outcomeLabel(row.market, leg.outcomeIndex, leg.point)}</span>
                          <span className="muted">{bookName(leg.bookId)}</span>
                          <span className="price">{fmtAmerican(leg.price)}</span>
                        </div>
                      ))}
                    </div>
                  </td>
                  <td className="num">{row.gap} {row.gap === 1 ? "pt" : "pts"}</td>
                  <td className="num green">+{fmtUnits(row.profitIfMiddle)}</td>
                  <td className={`num ${row.lossOtherwise > 0 ? "red" : "green"}`}>
                    {row.lossOtherwise > 0 ? `−${fmtUnits(row.lossOtherwise)}` : `+${fmtUnits(-row.lossOtherwise)}`}
                  </td>
                  <td className="num"><span className="pill pill-muted">{fmtPct(row.breakEven, 1)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <LockedBanner data={data} noun="middles" />
      </State>
    </>
  );
}
