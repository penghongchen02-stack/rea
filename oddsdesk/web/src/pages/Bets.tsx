import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type Bet, type BetStats } from "../api";
import { PageHeader, State } from "../components";
import { fmtAmerican, fmtMoney, fmtPct } from "../format";
import { useSession } from "../session";

export function BetsPage() {
  const { user, ready, meta } = useSession();
  const { data, error, loading, reload } = useApi<{ bets: Bet[]; stats: BetStats }>(user ? "/api/bets" : null);
  const [form, setForm] = useState({ description: "", book: "", americanOdds: "-110", stake: "10" });
  const [formError, setFormError] = useState<string | null>(null);

  if (!ready) return null;
  if (!user) {
    return (
      <>
        <PageHeader title="Bet Tracker" subtitle="Log your bets and track profit and ROI over time." />
        <div className="card">
          <Link to="/login">Log in</Link> or <Link to="/signup">create a free account</Link> to track bets.
        </div>
      </>
    );
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    try {
      await api("/api/bets", {
        method: "POST",
        body: { description: form.description, book: form.book, americanOdds: Number(form.americanOdds), stake: Number(form.stake) },
      });
      setForm({ ...form, description: "" });
      await reload();
    } catch (err) {
      setFormError((err as Error).message);
    }
  }

  async function setStatus(bet: Bet, status: Bet["status"]) {
    await api(`/api/bets/${bet.id}`, { method: "PATCH", body: { status } });
    await reload();
  }

  async function remove(bet: Bet) {
    await api(`/api/bets/${bet.id}`, { method: "DELETE" });
    await reload();
  }

  const stats = data?.stats;
  return (
    <>
      <PageHeader title="Bet Tracker" subtitle="Log your bets and track profit and ROI over time." />
      {stats && (
        <div className="stats">
          <Stat label="Bets" value={`${stats.total}`} sub={`${stats.open} open`} />
          <Stat label="Win rate" value={stats.settled ? fmtPct(stats.won / stats.settled, 1) : "—"} sub={`${stats.won}/${stats.settled} settled`} />
          <Stat label="Staked" value={fmtMoney(stats.staked)} sub="settled bets" />
          <Stat label="Profit" value={fmtMoney(stats.profit)} tone={stats.profit >= 0 ? "green" : "red"} sub={`ROI ${fmtPct(stats.roi, 1)}`} />
        </div>
      )}
      <form className="card form-row" onSubmit={(e) => void add(e)}>
        <label className="grow">
          Bet
          <input required placeholder="e.g. Metro Comets ML" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </label>
        <label>
          Book
          <input required list="books" value={form.book} onChange={(e) => setForm({ ...form, book: e.target.value })} />
          <datalist id="books">{meta?.books.map((b) => <option key={b.id} value={b.name} />)}</datalist>
        </label>
        <label>
          Odds
          <input required type="number" value={form.americanOdds} onChange={(e) => setForm({ ...form, americanOdds: e.target.value })} />
        </label>
        <label>
          Stake
          <input required type="number" min="0.01" step="0.01" value={form.stake} onChange={(e) => setForm({ ...form, stake: e.target.value })} />
        </label>
        <button className="btn btn-primary" type="submit">Add bet</button>
        {formError && <div className="error full">{formError}</div>}
      </form>
      <State loading={loading} error={error} empty={!data?.bets.length}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Placed</th>
                <th>Bet</th>
                <th>Book</th>
                <th className="num">Odds</th>
                <th className="num">Stake</th>
                <th>Result</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data?.bets.map((bet) => (
                <tr key={bet.id}>
                  <td className="muted small">{new Date(bet.createdAt).toLocaleDateString()}</td>
                  <td>{bet.description}</td>
                  <td>{bet.book}</td>
                  <td className="num price">{fmtAmerican(bet.americanOdds)}</td>
                  <td className="num">{fmtMoney(bet.stake)}</td>
                  <td>
                    <select className={`status status-${bet.status}`} value={bet.status} onChange={(e) => void setStatus(bet, e.target.value as Bet["status"])}>
                      <option value="open">Open</option>
                      <option value="won">Won</option>
                      <option value="lost">Lost</option>
                      <option value="push">Push</option>
                    </select>
                  </td>
                  <td>
                    <button className="btn btn-small btn-ghost" onClick={() => void remove(bet)} aria-label="Delete bet">✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </State>
    </>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: string }) {
  return (
    <div className="card stat">
      <div className="muted small">{label}</div>
      <div className={`stat-value ${tone ?? ""}`}>{value}</div>
      <div className="muted small">{sub}</div>
    </div>
  );
}
