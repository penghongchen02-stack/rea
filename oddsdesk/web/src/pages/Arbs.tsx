import { useState } from "react";
import { useApi, type ArbOpportunity, type Gated } from "../api";
import { Filters, LockedBanner, PageHeader, State, query, type Market } from "../components";
import { POLL_MS, fmtAmerican, fmtMoney, fmtPct, fmtTime, marketLabel, outcomeLabel } from "../format";
import { useSession } from "../session";

export function ArbsPage() {
  const { bookName } = useSession();
  const [sport, setSport] = useState("");
  const [market, setMarket] = useState<Market>("");
  const [total, setTotal] = useState(100);
  const { data, error, loading } = useApi<Gated<ArbOpportunity>>(`/api/arbs${query({ sport, market })}`, POLL_MS);

  return (
    <>
      <PageHeader
        title="Arbitrage Finder"
        subtitle="Combinations of best prices across books that guarantee a profit whichever side wins."
        updatedAt={data?.updatedAt}
      />
      <Filters sport={sport} market={market} onSport={setSport} onMarket={setMarket}>
        <label>
          Total stake
          <input type="number" min="1" value={total} onChange={(e) => setTotal(Number(e.target.value))} />
        </label>
      </Filters>
      <State loading={loading} error={error} empty={!data?.items.length && !data?.locked}>
        <div className="card-grid">
          {data?.items.map((arb) => (
            <div key={arb.id} className="card arb">
              <div className="arb-head">
                <div>
                  <div className="event-name">{arb.event.away} @ {arb.event.home}</div>
                  <div className="muted small">{fmtTime(arb.event.startTime)} · {marketLabel(arb.kind, arb.point)}</div>
                </div>
                <span className="pill pill-green">{fmtPct(arb.margin)}</span>
              </div>
              <table className="compact">
                <tbody>
                  {arb.legs.map((leg, i) => (
                    <tr key={i}>
                      <td>{outcomeLabel(arb.kind, arb.point, leg.outcome, i)}</td>
                      <td className="muted">{bookName(leg.bookId)}</td>
                      <td className="num price">{fmtAmerican(leg.price)}</td>
                      <td className="num">{fmtMoney(leg.stakeShare * total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="arb-foot">
                Guaranteed payout <strong>{fmtMoney(total * (1 + arb.margin))}</strong> · profit{" "}
                <strong className="green">{fmtMoney(total * arb.margin)}</strong>
              </div>
            </div>
          ))}
        </div>
        <LockedBanner locked={data?.locked ?? 0} noun="arbitrage" />
      </State>
    </>
  );
}
