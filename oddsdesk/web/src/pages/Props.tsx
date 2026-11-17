import { useState } from "react";
import type { BoardRow } from "../api";
import { BookPicker, PageHeader, State, useTool } from "../components";
import { fmtTime } from "../format";
import { useSession } from "../session";
import { OddsTable } from "./Odds";

export function PropsPage() {
  const { meta } = useSession();
  const sports = meta?.sports.filter((s) => s.propTypes.length) ?? [];
  const [sport, setSport] = useState("basketball");
  const [event, setEvent] = useState("");
  const [prop, setProp] = useState("");
  const current = sports.find((s) => s.id === sport);
  const propType = prop || current?.propTypes[0] || "";
  const events = meta?.events.filter((e) => e.sportId === sport) ?? [];
  const { data, error, loading, reload } = useTool<{ updatedAt: string; rows: BoardRow[] }>(
    "/api/odds",
    { sport, market: "player_prop" },
    { event, prop: propType },
  );

  return (
    <>
      <PageHeader
        title="Player Props"
        subtitle="Compare player over/unders across books. Books sometimes hang a different number; those prices are marked."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <div className="filters">
        <label>
          Sport
          <select value={sport} onChange={(e) => { setSport(e.target.value); setEvent(""); setProp(""); }}>
            {sports.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <label>
          Game
          <select value={event} onChange={(e) => setEvent(e.target.value)}>
            <option value="">All games</option>
            {events.map((e) => <option key={e.id} value={e.id}>{e.title} · {fmtTime(e.startTime)}</option>)}
          </select>
        </label>
        <label>
          Stat
          <select value={propType} onChange={(e) => setProp(e.target.value)}>
            {current?.propTypes.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <BookPicker />
      </div>
      <State loading={loading} error={error} empty={!data?.rows.length}>
        <OddsTable
          rows={data?.rows ?? []}
          firstColumn="Player"
          describe={(row) => ({ title: row.market.participant ?? "", detail: `${row.market.propType} · ${row.event.title}` })}
        />
      </State>
    </>
  );
}
