import type { MarketKind } from "../../server/types";

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

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

export function fmtPoint(point: number, signed: boolean): string {
  if (!signed) return `${point}`;
  return point > 0 ? `+${point}` : `${point}`;
}

const KIND_LABEL: Record<MarketKind, string> = { moneyline: "Moneyline", spread: "Spread", total: "Total" };

export function marketLabel(kind: MarketKind, point: number | null): string {
  if (point === null) return KIND_LABEL[kind];
  return `${KIND_LABEL[kind]} ${fmtPoint(point, kind === "spread")}`;
}

/** Outcome label including its line, e.g. "Metro Comets -5.5" or "Over 224.5". */
export function outcomeLabel(kind: MarketKind, point: number | null, outcome: string, index: number): string {
  if (point === null) return outcome;
  if (kind === "total") return `${outcome} ${point}`;
  return `${outcome} ${fmtPoint(index === 0 ? point : -point, true)}`;
}
