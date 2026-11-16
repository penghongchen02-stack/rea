import { randomUUID } from "node:crypto";
import express, { type NextFunction, type Request, type Response } from "express";
import { z } from "zod";
import { americanToDecimal } from "../shared/odds-math.js";
import { buildBoard, findArbs, findEv, type MarketFilter } from "./analysis.js";
import {
  SESSION_COOKIE,
  Sessions,
  hashPassword,
  publicUser,
  readCookie,
  requireUser,
  setSessionCookie,
  verifyPassword,
} from "./auth.js";
import { BOOKS, SPORTS, type SampleFeed } from "./sample-data.js";
import type { JsonStore } from "./store.js";
import type { Bet, User } from "./types.js";

/** What a free account sees before upgrading. */
export const FREE_LIMITS = { ev: 3, arbs: 1 } as const;

export interface AppDeps {
  feed: SampleFeed;
  store: JsonStore;
  secureCookies?: boolean;
}

const marketFilterSchema = z.object({
  sport: z.string().optional(),
  market: z.enum(["moneyline", "spread", "total"]).optional(),
});

const credentialsSchema = z.object({
  email: z.string().trim().toLowerCase().email().max(254),
  password: z.string().min(8, "Password must be at least 8 characters").max(200),
});

const americanSchema = z
  .number()
  .int()
  .refine((n) => Math.abs(n) >= 100, "American odds must be <= -100 or >= +100");

const newBetSchema = z.object({
  description: z.string().trim().min(1).max(200),
  book: z.string().trim().min(1).max(60),
  americanOdds: americanSchema,
  stake: z.number().positive().max(1_000_000),
});

const betUpdateSchema = z.object({ status: z.enum(["open", "won", "lost", "push"]) });

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

  app.get("/api/meta", (_req, res) => {
    res.json({
      sampleData: true,
      sports: SPORTS.map(({ id, name }) => ({ id, name })),
      books: BOOKS,
      version: feed.version,
      updatedAt: feed.updatedAt,
      freeLimits: FREE_LIMITS,
    });
  });

  app.get("/api/odds", (req, res) => {
    const filter = parseFilter(req);
    res.json({ updatedAt: feed.updatedAt, version: feed.version, rows: buildBoard(feed.listEvents(), feed.listMarkets(), filter) });
  });

  app.get("/api/ev", (req, res) => {
    const filter = parseFilter(req);
    const minEvPercent = z.coerce.number().min(-100).max(100).default(0).parse(req.query.minEv);
    const all = findEv(feed.listEvents(), feed.listMarkets(), BOOKS, { ...filter, minEv: minEvPercent / 100 });
    res.json({ updatedAt: feed.updatedAt, version: feed.version, ...gate(all, req.user, FREE_LIMITS.ev) });
  });

  app.get("/api/arbs", (req, res) => {
    const filter = parseFilter(req);
    const all = findArbs(feed.listEvents(), feed.listMarkets(), filter);
    res.json({ updatedAt: feed.updatedAt, version: feed.version, ...gate(all, req.user, FREE_LIMITS.arbs) });
  });

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
    const bets = store.bets.filter((b) => b.userId === req.user!.id).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
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

function parseFilter(req: Request): MarketFilter {
  const { sport, market } = marketFilterSchema.parse(req.query);
  return { sportId: sport || undefined, kind: market };
}

function gate<T>(items: T[], user: User | undefined, freeLimit: number): { items: T[]; total: number; locked: number } {
  const visible = user?.plan === "pro" ? items : items.slice(0, freeLimit);
  return { items: visible, total: items.length, locked: items.length - visible.length };
}

function ownBet(store: JsonStore, req: Request): Bet | undefined {
  return store.bets.find((b) => b.id === req.params.id && b.userId === req.user!.id) as Bet | undefined;
}

export function betStats(bets: readonly Bet[]) {
  let staked = 0;
  let profit = 0;
  let settled = 0;
  let won = 0;
  for (const bet of bets) {
    if (bet.status === "open") continue;
    settled++;
    staked += bet.stake;
    if (bet.status === "won") {
      won++;
      profit += bet.stake * (americanToDecimal(bet.americanOdds) - 1);
    } else if (bet.status === "lost") {
      profit -= bet.stake;
    }
  }
  return {
    total: bets.length,
    open: bets.length - settled,
    settled,
    won,
    staked: round2(staked),
    profit: round2(profit),
    roi: staked > 0 ? profit / staked : 0,
  };
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
