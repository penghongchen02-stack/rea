import { useCallback, useEffect, useRef, useState } from "react";
import type { ArbOpportunity, BoardRow, EvOpportunity } from "../../server/analysis";
import type { Bet, Book, Plan } from "../../server/types";

export type { ArbOpportunity, Bet, BoardRow, Book, EvOpportunity, Plan };

export interface PublicUser {
  id: string;
  email: string;
  plan: Plan;
  createdAt: string;
}

export interface Meta {
  sampleData: boolean;
  sports: { id: string; name: string }[];
  books: Book[];
  version: number;
  updatedAt: string;
  freeLimits: { ev: number; arbs: number };
}

export interface Gated<T> {
  updatedAt: string;
  items: T[];
  total: number;
  locked: number;
}

export interface BetStats {
  total: number;
  open: number;
  settled: number;
  won: number;
  staked: number;
  profit: number;
  roi: number;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(path, {
    method: init.method ?? "GET",
    headers: init.body === undefined ? undefined : { "content-type": "application/json" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    credentials: "same-origin",
  });
  if (res.status === 204) return undefined as T;
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, (json as { error?: string }).error ?? res.statusText);
  return json as T;
}

/** Fetch `path`, refetching whenever it changes and every `pollMs` while mounted. */
export function useApi<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const latest = useRef(path);
  latest.current = path;

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api<T>(path);
      if (latest.current === path) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (latest.current === path) setError((e as Error).message);
    } finally {
      if (latest.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setLoading(path !== null);
    void load();
    if (!pollMs || !path) return;
    const timer = setInterval(() => void load(), pollMs);
    return () => clearInterval(timer);
  }, [load, path, pollMs]);

  return { data, error, loading, reload: load };
}
