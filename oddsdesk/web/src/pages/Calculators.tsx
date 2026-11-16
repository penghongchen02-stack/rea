import { useState, type ReactNode } from "react";
import {
  americanToDecimal,
  arbitrage,
  decimalToAmerican,
  decimalToFractional,
  devig,
  expectedValue,
  hedge,
  impliedProbability,
  kellyFraction,
  overround,
  parlayDecimal,
} from "../../../shared/odds-math";
import { PageHeader } from "../components";
import { fmtAmerican, fmtMoney, fmtPct } from "../format";

const TABS = ["Odds Converter", "No-Vig & EV", "Kelly", "Arbitrage", "Parlay", "Hedge"] as const;
type Tab = (typeof TABS)[number];

export function CalculatorsPage() {
  const [tab, setTab] = useState<Tab>("Odds Converter");
  return (
    <>
      <PageHeader title="Calculators" subtitle="Betting math that runs in your browser. Enter American odds (e.g. -110 or +150)." />
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="card calc">
        {tab === "Odds Converter" && <Converter />}
        {tab === "No-Vig & EV" && <NoVig />}
        {tab === "Kelly" && <Kelly />}
        {tab === "Arbitrage" && <Arb />}
        {tab === "Parlay" && <Parlay />}
        {tab === "Hedge" && <Hedge />}
      </div>
    </>
  );
}

/** Parse American odds; null when not a valid price. */
function dec(american: string): number | null {
  const n = Number(american);
  if (!american.trim() || !Number.isFinite(n) || Math.abs(n) < 100) return null;
  return americanToDecimal(n);
}

function Field({ label, value, onChange, step }: { label: string; value: string; onChange(v: string): void; step?: string }) {
  return (
    <label>
      {label}
      <input type="number" step={step} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function Results({ rows, error }: { rows: [string, ReactNode][]; error?: string | null }) {
  if (error) return <div className="results error">{error}</div>;
  return (
    <dl className="results">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const INVALID = "Enter valid American odds (≤ -100 or ≥ +100).";

function Converter() {
  const [mode, setMode] = useState<"american" | "decimal" | "probability">("american");
  const [value, setValue] = useState("-110");
  let d: number | null = null;
  const n = Number(value);
  if (mode === "american") d = dec(value);
  else if (mode === "decimal") d = n > 1 ? n : null;
  else d = n > 0 && n < 100 ? 100 / n : null;
  return (
    <>
      <div className="form-row">
        <label>
          Input format
          <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
            <option value="american">American</option>
            <option value="decimal">Decimal</option>
            <option value="probability">Implied probability %</option>
          </select>
        </label>
        <Field label="Value" value={value} onChange={setValue} step="any" />
      </div>
      <Results
        error={d === null ? "Enter a valid value for the selected format." : null}
        rows={d === null ? [] : [
          ["American", fmtAmerican(decimalToAmerican(d))],
          ["Decimal", d.toFixed(3)],
          ["Fractional", decimalToFractional(d)],
          ["Implied probability", fmtPct(impliedProbability(d))],
        ]}
      />
    </>
  );
}

function NoVig() {
  const [a, setA] = useState("-120");
  const [b, setB] = useState("+100");
  const [offer, setOffer] = useState("+110");
  const da = dec(a);
  const db = dec(b);
  const doffer = dec(offer);
  if (!da || !db) {
    return <><Inputs2 a={a} b={b} setA={setA} setB={setB} offer={offer} setOffer={setOffer} /><Results rows={[]} error={INVALID} /></>;
  }
  const [pa, pb] = devig([da, db]) as [number, number];
  const rows: [string, ReactNode][] = [
    ["Market hold", fmtPct(overround([da, db]))],
    ["Fair side A", `${fmtAmerican(decimalToAmerican(1 / pa))} (${fmtPct(pa)})`],
    ["Fair side B", `${fmtAmerican(decimalToAmerican(1 / pb))} (${fmtPct(pb)})`],
  ];
  if (doffer) rows.push(["EV of offer on side A", <span className={expectedValue(pa, doffer) >= 0 ? "green" : "red"}>{fmtPct(expectedValue(pa, doffer))}</span>]);
  return <><Inputs2 a={a} b={b} setA={setA} setB={setB} offer={offer} setOffer={setOffer} /><Results rows={rows} /></>;
}

function Inputs2(p: { a: string; b: string; offer: string; setA(v: string): void; setB(v: string): void; setOffer(v: string): void }) {
  return (
    <div className="form-row">
      <Field label="Sharp price, side A" value={p.a} onChange={p.setA} />
      <Field label="Sharp price, side B" value={p.b} onChange={p.setB} />
      <Field label="Your offer on side A" value={p.offer} onChange={p.setOffer} />
    </div>
  );
}

function Kelly() {
  const [odds, setOdds] = useState("+150");
  const [prob, setProb] = useState("45");
  const [bankroll, setBankroll] = useState("1000");
  const [mult, setMult] = useState("0.25");
  const d = dec(odds);
  const p = Number(prob) / 100;
  const valid = d && p > 0 && p < 1;
  const f = valid ? kellyFraction(p, d) : 0;
  return (
    <>
      <div className="form-row">
        <Field label="Odds" value={odds} onChange={setOdds} />
        <Field label="Win probability %" value={prob} onChange={setProb} step="0.1" />
        <Field label="Bankroll" value={bankroll} onChange={setBankroll} />
        <label>
          Kelly multiplier
          <select value={mult} onChange={(e) => setMult(e.target.value)}>
            <option value="1">Full</option>
            <option value="0.5">Half</option>
            <option value="0.25">Quarter</option>
            <option value="0.1">Tenth</option>
          </select>
        </label>
      </div>
      <Results
        error={valid ? null : "Enter valid odds and a probability between 0 and 100."}
        rows={valid ? [
          ["Edge (EV)", fmtPct(expectedValue(p, d))],
          ["Full Kelly", fmtPct(f)],
          ["Recommended stake", fmtMoney(Number(bankroll) * f * Number(mult))],
        ] : []}
      />
    </>
  );
}

function Arb() {
  const [a, setA] = useState("+110");
  const [b, setB] = useState("-102");
  const [total, setTotal] = useState("100");
  const da = dec(a);
  const db = dec(b);
  const plan = da && db ? arbitrage([da, db], Number(total)) : null;
  return (
    <>
      <div className="form-row">
        <Field label="Side A odds" value={a} onChange={setA} />
        <Field label="Side B odds" value={b} onChange={setB} />
        <Field label="Total stake" value={total} onChange={setTotal} />
      </div>
      <Results
        error={plan ? null : INVALID}
        rows={plan ? [
          ["Arbitrage?", plan.isArbitrage ? <span className="green">Yes</span> : <span className="red">No</span>],
          ["Stake side A", fmtMoney(plan.stakes[0]!)],
          ["Stake side B", fmtMoney(plan.stakes[1]!)],
          ["Payout either way", fmtMoney(plan.payout)],
          ["Profit", <span className={plan.isArbitrage ? "green" : "red"}>{fmtMoney(plan.payout - Number(total))} ({fmtPct(plan.profitMargin)})</span>],
        ] : []}
      />
    </>
  );
}

function Parlay() {
  const [legs, setLegs] = useState(["-110", "+150", "-200"]);
  const [stake, setStake] = useState("10");
  const decimals = legs.map(dec);
  const valid = decimals.every((d): d is number => d !== null);
  const d = valid ? parlayDecimal(decimals) : null;
  return (
    <>
      <div className="form-row">
        {legs.map((leg, i) => (
          <Field key={i} label={`Leg ${i + 1}`} value={leg} onChange={(v) => setLegs(legs.map((l, j) => (j === i ? v : l)))} />
        ))}
        <Field label="Stake" value={stake} onChange={setStake} />
      </div>
      <div className="form-row">
        <button className="btn btn-ghost" onClick={() => setLegs([...legs, "-110"])} disabled={legs.length >= 12}>+ Add leg</button>
        <button className="btn btn-ghost" onClick={() => setLegs(legs.slice(0, -1))} disabled={legs.length <= 2}>− Remove leg</button>
      </div>
      <Results
        error={d ? null : INVALID}
        rows={d ? [
          ["Parlay odds", `${fmtAmerican(decimalToAmerican(d))} (${d.toFixed(2)})`],
          ["Implied probability", fmtPct(1 / d)],
          ["Payout", fmtMoney(Number(stake) * d)],
          ["Profit", fmtMoney(Number(stake) * (d - 1))],
        ] : []}
      />
    </>
  );
}

function Hedge() {
  const [stake, setStake] = useState("100");
  const [original, setOriginal] = useState("+300");
  const [hedgeOdds, setHedgeOdds] = useState("-150");
  const d1 = dec(original);
  const d2 = dec(hedgeOdds);
  const plan = d1 && d2 ? hedge(Number(stake), d1, d2) : null;
  return (
    <>
      <div className="form-row">
        <Field label="Original stake" value={stake} onChange={setStake} />
        <Field label="Original odds" value={original} onChange={setOriginal} />
        <Field label="Hedge odds" value={hedgeOdds} onChange={setHedgeOdds} />
      </div>
      <Results
        error={plan ? null : INVALID}
        rows={plan ? [
          ["Hedge stake", fmtMoney(plan.hedgeStake)],
          ["Locked profit", <span className={plan.profitIfOriginalWins >= 0 ? "green" : "red"}>{fmtMoney(plan.profitIfOriginalWins)}</span>],
        ] : []}
      />
    </>
  );
}
