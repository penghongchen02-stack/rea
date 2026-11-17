import { useState } from "react";
import type { BoardRow } from "../api";
import { EventCell, Filters, PageHeader, State, useTool, type ToolFilters } from "../components";
import { fmtAmerican, lineTag, marketLabel, outcomeLabel } from "../format";
import { usePrefs } from "../prefs";
import { useSession } from "../session";

export function OddsPage() {
  const [filters, setFilters] = useState<ToolFilters>({ sport: "", market: "moneyline", period: "full" });
  const { data, error, loading, reload } = useTool<{ updatedAt: string; rows: BoardRow[] }>("/api/odds", filters);

  return (
    <>
      <PageHeader
        title="Compare Odds"
        subtitle="Every book's line and price side by side. The best price on the main line is highlighted."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <Filters value={filters} onChange={setFilters} markets={["moneyline", "spread", "total", "team_total"]} />
      <State loading={loading} error={error} empty={!data?.rows.length}>
        <OddsTable rows={data?.rows ?? []} />
      </State>
    </>
  );
}

/** Book-by-book price grid; also used for player props and futures. */
export function OddsTable({ rows, firstColumn = "Event", describe }: {
  rows: BoardRow[];
  firstColumn?: string;
  describe?(row: BoardRow): { title: string; detail?: string };
}) {
  const { meta } = useSession();
  const { prefs } = usePrefs();
  const books = (meta?.books ?? []).filter((b) => !prefs.books.length || prefs.books.includes(b.id));

  return (
    <div className="table-wrap">
      <table className="odds-table">
        <thead>
          <tr>
            <th>{firstColumn}</th>
            <th>Bet</th>
            <th className="num">Best</th>
            {books.map((b) => (
              <th key={b.id} className="num">
                {b.name}
                {b.sharp && <span className="tag">sharp</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const head = describe?.(row) ?? { title: row.event.title, detail: marketLabel(row.market) };
            return row.market.outcomes.map((_, i) => (
              <tr key={`${row.market.id}:${i}`} className={i === 0 ? "group-start" : ""}>
                {i === 0 && (
                  <td rowSpan={row.market.outcomes.length} className="event-cell">
                    <EventCell title={head.title} startTime={row.event.startTime} detail={head.detail} />
                  </td>
                )}
                <td>{outcomeLabel(row.market, i, row.market.point)}</td>
                <td className="num price best-col">
                  {fmtAmerican(row.best[i]?.price)}
                </td>
                {books.map((b) => {
                  const offer = row.market.offers[b.id];
                  const price = offer?.prices[i];
                  const offMain = offer && offer.point !== row.market.point;
                  const best = !offMain && row.best[i]?.bookIds.includes(b.id);
                  return (
                    <td key={b.id} className={`num price ${best ? "best" : ""} ${offMain ? "alt-line" : ""}`} title={offMain ? "Different line from the market" : undefined}>
                      {offer && offer.point !== null && <span className="line-tag">{lineTag(row.market.kind, i, offer.point)}</span>}
                      {fmtAmerican(price)}
                    </td>
                  );
                })}
              </tr>
            ));
          })}
        </tbody>
      </table>
    </div>
  );
}
