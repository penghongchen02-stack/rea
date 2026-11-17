import { useState } from "react";
import type { Gated, HoldRow } from "../api";
import { EventCell, Filters, LockedBanner, PageHeader, State, useTool, type ToolFilters } from "../components";
import { fmtAmerican, fmtMoney, fmtPct, marketLabel, outcomeLabel } from "../format";
import { useSession } from "../session";

const ALL_MARKETS = ["", "moneyline", "spread", "total", "team_total", "player_prop", "future"] as const;

export function ArbsPage() {
  const [filters, setFilters] = useState<ToolFilters>({ sport: "", market: "", period: "" });
  const [total, setTotal] = useState(100);
  const { data, error, loading, reload } = useTool<Gated<HoldRow>>("/api/arbs", filters);

  return (
    <>
      <PageHeader
        title="Arbitrage"
        subtitle="Best prices across books that pay out more than you stake whichever side wins."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <Filters value={filters} onChange={setFilters} markets={[...ALL_MARKETS]}>
        <StakeInput value={total} onChange={setTotal} />
      </Filters>
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <HoldTable rows={data?.items ?? []} total={total} />
        <LockedBanner data={data} noun="arbitrage bets" />
      </State>
    </>
  );
}

export function LowHoldPage() {
  const [filters, setFilters] = useState<ToolFilters>({ sport: "", market: "", period: "" });
  const [total, setTotal] = useState(100);
  const { data, error, loading, reload } = useTool<Gated<HoldRow>>("/api/low-hold", filters);

  return (
    <>
      <PageHeader
        title="Low Hold"
        subtitle="Markets where the best prices across books leave the smallest margin. Useful for clearing bonuses and playthrough cheaply; a negative hold is an arbitrage."
        updatedAt={data?.updatedAt}
        onRefresh={() => void reload()}
      />
      <Filters value={filters} onChange={setFilters} markets={[...ALL_MARKETS]}>
        <StakeInput value={total} onChange={setTotal} />
      </Filters>
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <HoldTable rows={data?.items ?? []} total={total} showHold />
        <LockedBanner data={data} noun="low-hold markets" />
      </State>
    </>
  );
}

function StakeInput({ value, onChange }: { value: number; onChange(v: number): void }) {
  return (
    <label>
      Total stake
      <input type="number" min="1" value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

function HoldTable({ rows, total, showHold = false }: { rows: HoldRow[]; total: number; showHold?: boolean }) {
  const { bookName } = useSession();
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Bets</th>
            <th className="num">Stake</th>
            <th className="num">Payout</th>
            <th className="num">Profit</th>
            <th className="num">{showHold ? "Hold" : "Return"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const payout = total / (1 + row.hold);
            const profit = payout - total;
            return (
              <tr key={row.id}>
                <td><EventCell title={row.event.title} startTime={row.event.startTime} detail={marketLabel(row.market)} /></td>
                <td>
                  <div className="legs">
                    {row.legs.map((leg) => (
                      <div key={leg.outcomeIndex} className="leg">
                        <span>{outcomeLabel(row.market, leg.outcomeIndex, row.point)}</span>
                        <span className="muted">{bookName(leg.bookId)}</span>
                        <span className="price">{fmtAmerican(leg.price)}</span>
                      </div>
                    ))}
                  </div>
                </td>
                <td className="num">
                  <div className="legs">{row.legs.map((leg) => <div key={leg.outcomeIndex}>{fmtMoney(leg.stakeShare * total)}</div>)}</div>
                </td>
                <td className="num">{fmtMoney(payout)}</td>
                <td className={`num ${profit >= 0 ? "green" : "red"}`}>{fmtMoney(profit)}</td>
                <td className="num">
                  <span className={`pill ${row.hold < 0 ? "pill-green" : "pill-muted"}`}>
                    {showHold ? fmtPct(row.hold) : fmtPct(-row.hold / (1 + row.hold))}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
