import { randomUUID } from "node:crypto";
import express, { type NextFunction, type Request, type Response } from "express";
import { z } from "zod";
import {
  SESSION_COOKIE,
  Sessions,
  hashPassword,
  readCookie,
  requireUser,
  setSessionCookie,
  verifyPassword,
} from "./auth.js";
import type { SampleFeed } from "./sample-data.js";
import { readRoutes } from "./service.js";
import {
  FREE_LIMITS,
  betStats,
  betUpdateSchema,
  credentialsSchema,
  newBetSchema,
  publicUser,
  sortBets,
} from "./rules.js";
import type { JsonStore } from "./store.js";
import type { Bet, User } from "./types.js";

export { FREE_LIMITS };

export interface AppDeps {
  feed: SampleFeed;
  store: JsonStore;
  secureCookies?: boolean;
}

export function createApp({ feed, store, secureCookies = false }: AppDeps): express.Express {
  const app = express();
  const sessions = new Sessions();

  app.disable("x-powered-by");
  app.use(express.json({ limit: "16kb" }));
  app.use((req, _res, next) => {
    const userId = sessions.resolve(readCookie(req, SESSION_COOKIE));
    req.user = store.users.find((u) => u.id === userId);
    next();
  });

  app.get("/api/health", (_req, res) => {
    res.json({ ok: true });
  });

  for (const [path, handler] of Object.entries(readRoutes(feed))) {
    app.get(path, (req, res) => {
      res.json(handler(req.query, req.user));
    });
  }

  // --- Accounts -------------------------------------------------------------

  app.post("/api/auth/signup", async (req, res) => {
    const { email, password } = credentialsSchema.parse(req.body);
    if (store.users.some((u) => u.email === email)) {
      res.status(409).json({ error: "An account with that email already exists" });
      return;
    }
    const user: User = { id: randomUUID(), email, passwordHash: await hashPassword(password), plan: "free", createdAt: new Date().toISOString() };
    await store.mutate((d) => void d.users.push(user));
    setSessionCookie(res, sessions.create(user.id), secureCookies);
    res.status(201).json({ user: publicUser(user) });
  });

  app.post("/api/auth/login", async (req, res) => {
    const { email, password } = credentialsSchema.parse(req.body);
    const user = store.users.find((u) => u.email === email);
    if (!user || !(await verifyPassword(password, user.passwordHash))) {
      res.status(401).json({ error: "Incorrect email or password" });
      return;
    }
    setSessionCookie(res, sessions.create(user.id), secureCookies);
    res.json({ user: publicUser(user) });
  });

  app.post("/api/auth/logout", (req, res) => {
    sessions.destroy(readCookie(req, SESSION_COOKIE));
    res.clearCookie(SESSION_COOKIE, { path: "/" });
    res.json({ ok: true });
  });

  app.get("/api/auth/me", (req, res) => {
    res.json({ user: req.user ? publicUser(req.user) : null });
  });

  // --- Demo billing: switches plans without taking payment -------------------

  app.post("/api/billing/checkout", requireUser, async (req, res) => {
    const user = req.user!;
    await store.mutate(() => void (user.plan = "pro"));
    res.json({ user: publicUser(user), demo: true });
  });

  app.post("/api/billing/cancel", requireUser, async (req, res) => {
    const user = req.user!;
    await store.mutate(() => void (user.plan = "free"));
    res.json({ user: publicUser(user), demo: true });
  });

  // --- Bet tracker ----------------------------------------------------------

  app.get("/api/bets", requireUser, (req, res) => {
    const bets = sortBets(store.bets.filter((b) => b.userId === req.user!.id));
    res.json({ bets, stats: betStats(bets) });
  });

  app.post("/api/bets", requireUser, async (req, res) => {
    const input = newBetSchema.parse(req.body);
    const bet: Bet = { id: randomUUID(), userId: req.user!.id, createdAt: new Date().toISOString(), status: "open", ...input };
    await store.mutate((d) => void d.bets.push(bet));
    res.status(201).json({ bet });
  });

  app.patch("/api/bets/:id", requireUser, async (req, res) => {
    const { status } = betUpdateSchema.parse(req.body);
    const bet = ownBet(store, req);
    if (!bet) {
      res.status(404).json({ error: "Bet not found" });
      return;
    }
    await store.mutate(() => void (bet.status = status));
    res.json({ bet });
  });

  app.delete("/api/bets/:id", requireUser, async (req, res) => {
    const bet = ownBet(store, req);
    if (!bet) {
      res.status(404).json({ error: "Bet not found" });
      return;
    }
    await store.mutate((d) => void (d.bets = d.bets.filter((b) => b.id !== bet.id)));
    res.status(204).end();
  });

  app.use("/api", (_req, res) => {
    res.status(404).json({ error: "Not found" });
  });

  app.use((error: unknown, _req: Request, res: Response, _next: NextFunction) => {
    if (error instanceof z.ZodError) {
      res.status(400).json({ error: error.issues[0]?.message ?? "Invalid request", issues: error.issues });
      return;
    }
    if (error instanceof SyntaxError && "body" in error) {
      res.status(400).json({ error: "Malformed JSON body" });
      return;
    }
    console.error(error);
    res.status(500).json({ error: "Internal server error" });
  });

  return app;
}

function ownBet(store: JsonStore, req: Request): Bet | undefined {
  return store.bets.find((b) => b.id === req.params.id && b.userId === req.user!.id) as Bet | undefined;
}
