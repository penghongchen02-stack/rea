import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

/** Per-viewer settings shared by every tool. */
export interface Prefs {
  /** Books the viewer uses; empty means all books. */
  books: string[];
  autoRefresh: boolean;
  bankroll: number;
  kellyMultiplier: number;
}

const DEFAULTS: Prefs = { books: [], autoRefresh: true, bankroll: 1000, kellyMultiplier: 0.25 };
const KEY = "linescout-prefs-v1";

function load(): Prefs {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Prefs>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

const PrefsContext = createContext<{ prefs: Prefs; update(change: Partial<Prefs>): void } | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(load);
  const update = useCallback((change: Partial<Prefs>) => {
    setPrefs((current) => {
      const next = { ...current, ...change };
      try {
        window.localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: keep settings for this visit */
      }
      return next;
    });
  }, []);
  const value = useMemo(() => ({ prefs, update }), [prefs, update]);
  return <PrefsContext.Provider value={value}>{children}</PrefsContext.Provider>;
}

export function usePrefs() {
  const context = useContext(PrefsContext);
  if (!context) throw new Error("usePrefs outside PrefsProvider");
  return context;
}
