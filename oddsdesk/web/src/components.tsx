import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { MarketKind, Period } from "../../server/types";
import { useApi, type Gated } from "./api";
import { POLL_MS, fmtAgo } from "./format";
import { usePrefs } from "./prefs";
import { useSession } from "./session";

export function PageHeader({ title, subtitle, updatedAt, onRefresh }: { title: string; subtitle: string; updatedAt?: string; onRefresh?(): void }) {
  const { prefs, update } = usePrefs();
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="muted">{subtitle}</p>
      </div>
      {updatedAt && (
        <div className="page-header-side">
          <span className="live">
            <span className={`dot ${prefs.autoRefresh ? "" : "dot-off"}`} /> Updated {new Date(updatedAt).toLocaleTimeString()}
          </span>
          <label className="switch" title="Refresh prices every 15 seconds">
            <input type="checkbox" checked={prefs.autoRefresh} onChange={(e) => update({ autoRefresh: e.target.checked })} />
            Auto-refresh
          </label>
          {onRefresh && (
            <button className="btn btn-small btn-ghost" onClick={onRefresh} aria-label="Refresh now">↻</button>
          )}
        </div>
      )}
    </div>
  );
}

export type KindFilter = "" | MarketKind;

export interface ToolFilters {
  sport: string;
  market: KindFilter;
  period: "" | Period;
}

/** Sport, market, period and sportsbook pickers shared by the odds tools. */
export function Filters({ value, onChange, markets, children }: {
  value: ToolFilters;
  onChange(next: ToolFilters): void;
  /** Market kinds to offer; empty string means "All markets". */
  markets: KindFilter[];
  children?: ReactNode;
}) {
  const { meta } = useSession();
  const sport = meta?.sports.find((s) => s.id === value.sport);
  const periods = sport?.periods ?? uniquePeriods(meta?.sports ?? []);
  return (
    <div className="filters">
      <label>
        Sport
        <select value={value.sport} onChange={(e) => onChange({ ...value, sport: e.target.value, period: "" })}>
          <option value="">All sports</option>
          {meta?.sports.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <label>
        Market
        <select value={value.market} onChange={(e) => onChange({ ...value, market: e.target.value as KindFilter })}>
          {markets.map((m) => <option key={m} value={m}>{m ? MARKET_NAMES[m] : "All markets"}</option>)}
        </select>
      </label>
      <label>
        Period
        <select value={value.period} onChange={(e) => onChange({ ...value, period: e.target.value as ToolFilters["period"] })}>
          <option value="">All periods</option>
          {periods.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <BookPicker />
      {children}
    </div>
  );
}

const MARKET_NAMES: Record<MarketKind, string> = {
  moneyline: "Moneyline",
  spread: "Spread",
  total: "Total",
  team_total: "Team total",
  player_prop: "Player props",
  future: "Futures",
};

function uniquePeriods(sports: { periods: { id: Period; name: string }[] }[]) {
  const seen = new Map<Period, string>();
  for (const s of sports) for (const p of s.periods) seen.set(p.id, p.name);
  return [...seen].map(([id, name]) => ({ id, name }));
}

/** Choose which sportsbooks every tool compares; saved per viewer. */
export function BookPicker() {
  const { meta } = useSession();
  const { prefs, update } = usePrefs();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const books = meta?.books ?? [];
  const selected = prefs.books.length ? prefs.books : books.map((b) => b.id);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function toggle(id: string) {
    const next = selected.includes(id) ? selected.filter((b) => b !== id) : [...selected, id];
    if (next.length === 0) return;
    update({ books: next.length === books.length ? [] : next });
  }

  return (
    <div className="book-picker" ref={ref}>
      <span className="picker-label">Sportsbooks</span>
      <button type="button" className="picker-btn" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {prefs.books.length ? `${prefs.books.length} of ${books.length}` : "All books"} ▾
      </button>
      {open && (
        <div className="picker-menu" role="menu">
          {books.map((b) => (
            <label key={b.id} className="picker-item">
              <input type="checkbox" checked={selected.includes(b.id)} onChange={() => toggle(b.id)} />
              {b.name}
              {b.sharp && <span className="tag inline">sharp</span>}
            </label>
          ))}
          <button type="button" className="btn btn-small btn-ghost" onClick={() => update({ books: [] })}>Select all</button>
        </div>
      )}
    </div>
  );
}

export function query(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Fetch a tool endpoint with the shared filters and the viewer's book selection. */
export function useTool<T>(path: string, filters: Partial<ToolFilters>, extra: Record<string, string | number | undefined> = {}) {
  const { prefs } = usePrefs();
  return useApi<T>(
    `${path}${query({ ...filters, ...extra, books: prefs.books.join(",") || undefined })}`,
    prefs.autoRefresh ? POLL_MS : undefined,
  );
}

export function LockedBanner({ data, noun }: { data: Gated<unknown> | null; noun: string }) {
  const { user } = useSession();
  if (!data || data.locked <= 0) return null;
  return (
    <div className="locked">
      <div>
        <strong>Showing {data.items.length} of {data.total} {noun}.</strong>{" "}
        {user ? "Pro shows every result and updates." : "Create a free account, then upgrade to Pro to see every result."}
      </div>
      <Link to={user ? "/pricing" : "/signup"} className="btn btn-primary">{user ? "Upgrade to Pro" : "Sign up free"}</Link>
    </div>
  );
}

export function State({ loading, error, empty, children }: { loading: boolean; error: string | null; empty: boolean; children: ReactNode }) {
  if (error) return <div className="card error">Couldn't load data: {error}</div>;
  if (loading) return <div className="card muted">Loading…</div>;
  if (empty) return <div className="card muted">Nothing matches these filters right now. Prices refresh every 15 seconds.</div>;
  return <>{children}</>;
}

export function EventCell({ title, startTime, detail }: { title: string; startTime: string; detail?: string }) {
  return (
    <div>
      <div className="event-name">{title}</div>
      <div className="muted small">
        {new Date(startTime).toLocaleString(undefined, { weekday: "short", month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" })}
        {detail && ` · ${detail}`}
      </div>
    </div>
  );
}

export { fmtAgo };
