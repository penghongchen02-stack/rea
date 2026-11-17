import { outcomePoint, type MarketRef } from "../../server/analysis";
import { PERIOD_LABEL, type MarketKind } from "../../server/types";

export const POLL_MS = 15_000;

export function fmtAmerican(price: number | null | undefined): string {
  if (price === null || price === undefined || !Number.isFinite(price)) return "—";
  return price > 0 ? `+${price}` : `${price}`;
}

export function fmtPct(value: number, digits = 2): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function fmtMoney(value: number): string {
  return value.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

export function fmtUnits(value: number): string {
  return `${value.toFixed(2)}u`;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

export function fmtAgo(iso: string): string {
  const minutes = Math.round((Date.now() - Date.parse(iso)) / 60_000);
  if (!Number.isFinite(minutes)) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.round(minutes / 60)}h ago`;
}

export function fmtSigned(point: number): string {
  return point > 0 ? `+${point}` : `${point}`;
}

export const KIND_LABEL: Record<MarketKind, string> = {
  moneyline: "Moneyline",
  spread: "Spread",
  total: "Total",
  team_total: "Team total",
  player_prop: "Player prop",
  future: "Futures",
};

/** Market name as a bettor reads it, e.g. "Spread · 1st half" or "Points · Avery Marsh (Metro Comets)". */
export function marketLabel(market: Pick<MarketRef, "kind" | "period" | "participant" | "propType">): string {
  const base = market.kind === "player_prop" ? market.propType ?? "Player prop" : KIND_LABEL[market.kind];
  const parts = [base];
  if (market.participant) parts.push(market.participant);
  if (market.period !== "full") parts.push(PERIOD_LABEL[market.period]);
  return parts.join(" · ");
}

/** Outcome with its line, e.g. "Metro Comets -5.5" or "Over 224.5". */
export function outcomeLabel(market: Pick<MarketRef, "kind" | "outcomes">, outcomeIndex: number, point: number | null): string {
  const name = market.outcomes[outcomeIndex] ?? "";
  const p = outcomePoint(market.kind, point, outcomeIndex);
  if (p === null) return market.kind === "moneyline" && name !== "Draw" ? `${name} ML` : name;
  return market.kind === "spread" ? `${name} ${fmtSigned(p)}` : `${name} ${p}`;
}

/** Short line for a price cell, e.g. "-5.5", "o224.5" or "u224.5". */
export function lineTag(kind: MarketKind, outcomeIndex: number, point: number | null): string {
  const p = outcomePoint(kind, point, outcomeIndex);
  if (p === null) return "";
  if (kind === "spread") return fmtSigned(p);
  return `${outcomeIndex === 0 ? "o" : "u"}${p}`;
}
