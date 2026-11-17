import { useState } from "react";
import type { BoardRow } from "../api";
import { BookPicker, PageHeader, State, useTool } from "../components";
import { fmtAmerican, fmtPct } from "../format";
import { usePrefs } from "../prefs";
import { useSession } from "../session";
import { americanToDecimal } from "../../../shared/odds-math";

export function FuturesPage() {
  const { meta } = useSession();
  const { prefs } = usePrefs();
  const [sport, setSport] = useState("football");
  const { data, error, loading, reload } = useTool<{ updatedAt: string; rows: BoardRow[] }>("/api/odds", { sport, market: "future" });
  const books = (meta?.books ?? []).filter((b) => !prefs.books.length || prefs.books.includes(b.id));
  const row = data?.rows[0];

  // Rank contenders by their best price, shortest first.
  const order = row
    ? row.market.outcomes
        .map((name, i) => ({ name, i, best: row.best[i]?.price ?? null }))
        .sort((a, b) => (a.best === null ? 1 : b.best === null ? -1 : americanToDecimal(a.best) - americanToDecimal(b.best)))
    : [];

  return (
    <>
      <PageHeader
        title="Futures"
        subtitle="Season-long championship prices. Futures carry much more margin than single games, so shopping for the best price matters."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <div className="filters">
        <label>
          Sport
          <select value={sport} onChange={(e) => setSport(e.target.value)}>
            {meta?.sports.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <BookPicker />
      </div>
      <State loading={loading} error={error} empty={!row}>
        {row && (
          <div className="table-wrap">
            <table className="odds-table">
              <thead>
                <tr>
                  <th>{row.event.title}</th>
                  <th className="num">Best</th>
                  <th className="num">Implied</th>
                  {books.map((b) => (
                    <th key={b.id} className="num">
                      {b.name}
                      {b.sharp && <span className="tag">sharp</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {order.map(({ name, i, best }) => (
                  <tr key={name}>
                    <td className="event-name">{name}</td>
                    <td className="num price best-col">{fmtAmerican(best)}</td>
                    <td className="num muted">{best === null ? "—" : fmtPct(1 / americanToDecimal(best), 1)}</td>
                    {books.map((b) => (
                      <td key={b.id} className={`num price ${row.best[i]?.bookIds.includes(b.id) ? "best" : ""}`}>
                        {fmtAmerican(row.market.offers[b.id]?.prices[i])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </State>
    </>
  );
}
