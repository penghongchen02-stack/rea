import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useSession } from "./session";

export function PageHeader({ title, subtitle, updatedAt, children }: { title: string; subtitle: string; updatedAt?: string; children?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="muted">{subtitle}</p>
      </div>
      <div className="page-header-side">
        {updatedAt && (
          <span className="live">
            <span className="dot" /> Updated {new Date(updatedAt).toLocaleTimeString()}
          </span>
        )}
        {children}
      </div>
    </div>
  );
}

export type Market = "" | "moneyline" | "spread" | "total";

export function Filters({ sport, market, onSport, onMarket, children }: {
  sport: string;
  market: Market;
  onSport(v: string): void;
  onMarket(v: Market): void;
  children?: ReactNode;
}) {
  const { meta } = useSession();
  return (
    <div className="filters">
      <label>
        Sport
        <select value={sport} onChange={(e) => onSport(e.target.value)}>
          <option value="">All sports</option>
          {meta?.sports.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </label>
      <label>
        Market
        <select value={market} onChange={(e) => onMarket(e.target.value as Market)}>
          <option value="">All markets</option>
          <option value="moneyline">Moneyline</option>
          <option value="spread">Spread</option>
          <option value="total">Total</option>
        </select>
      </label>
      {children}
    </div>
  );
}

export function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function LockedBanner({ locked, noun }: { locked: number; noun: string }) {
  const { user } = useSession();
  if (locked <= 0) return null;
  return (
    <div className="locked">
      <div>
        <strong>{locked} more {noun}{locked === 1 ? "" : "s"} hidden.</strong> Free accounts see the top results only.
      </div>
      <Link to={user ? "/pricing" : "/signup"} className="btn btn-primary">
        {user ? "Upgrade to Pro" : "Sign up free"}
      </Link>
    </div>
  );
}

export function State({ loading, error, empty, children }: { loading: boolean; error: string | null; empty: boolean; children: ReactNode }) {
  if (error) return <div className="card error">Couldn't load data: {error}</div>;
  if (loading) return <div className="card muted">Loading…</div>;
  if (empty) return <div className="card muted">Nothing matches these filters right now. The feed refreshes every few seconds.</div>;
  return <>{children}</>;
}
