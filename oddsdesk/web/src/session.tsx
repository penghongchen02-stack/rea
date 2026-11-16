import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, useApi, type Meta, type PublicUser } from "./api";

interface Session {
  user: PublicUser | null;
  ready: boolean;
  meta: Meta | null;
  setUser(user: PublicUser | null): void;
  logout(): Promise<void>;
  bookName(id: string): string;
}

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<PublicUser | null>(null);
  const [ready, setReady] = useState(false);
  const { data: meta } = useApi<Meta>("/api/meta");

  useEffect(() => {
    api<{ user: PublicUser | null }>("/api/auth/me")
      .then((r) => setUser(r.user))
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, []);

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" });
    setUser(null);
  }, []);

  const value = useMemo<Session>(() => {
    const names = new Map(meta?.books.map((b) => [b.id, b.name]));
    return { user, ready, meta, setUser, logout, bookName: (id) => names.get(id) ?? id };
  }, [user, ready, meta, logout]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}
