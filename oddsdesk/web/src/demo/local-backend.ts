/**
 * In-browser stand-in for the Express API, used by the standalone demo build.
 * It serves the same routes and response shapes from the same feed, analysis
 * and rules modules; accounts and bets live in this browser's localStorage.
 */
import { z } from "zod";
import { buildBoard, findArbs, findEv } from "../../../server/analysis";
import {
  FREE_LIMITS,
  betStats,
  betUpdateSchema,
  credentialsSchema,
  gate,
  marketFilterSchema,
  minEvSchema,
  newBetSchema,
  publicUser,
  sortBets,
} from "../../../server/rules";
import { BOOKS, SPORTS, SampleFeed } from "../../../server/sample-data";
import type { Bet, User } from "../../../server/types";

interface Data {
  users: User[];
  bets: Bet[];
  sessionUserId: string | null;
}

const STORAGE_KEY = "linescout-demo-v1";
const TICK_MS = 15_000;

let feed: SampleFeed | null = null;
let memory: Data = { users: [], bets: [], sessionUserId: null };

function getFeed(): SampleFeed {
  if (!feed) {
    feed = new SampleFeed();
    const live = feed;
    setInterval(() => live.tick(), TICK_MS);
  }
  return feed;
}

/** localStorage can be missing or throw (private mode, blocked storage); fall back to memory. */
function load(): Data {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) memory = JSON.parse(raw) as Data;
  } catch {
    /* keep the in-memory copy */
  }
  return memory;
}

function save(data: Data): void {
  memory = data;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* storage unavailable: the session keeps working in memory */
  }
}

async function hashPassword(password: string, saltHex?: string): Promise<string> {
  const salt: Uint8Array<ArrayBuffer> = saltHex ? hexToBytes(saltHex) : crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: 120_000 }, key, 256);
  return `pbkdf2$${bytesToHex(salt)}$${bytesToHex(new Uint8Array(bits))}`;
}

async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, salt] = stored.split("$");
  if (scheme !== "pbkdf2" || !salt) return false;
  return (await hashPassword(password, salt)) === stored;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(hex: string): Uint8Array<ArrayBuffer> {
  return new Uint8Array(hex.match(/../g)?.map((h) => parseInt(h, 16)) ?? []);
}

interface Reply {
  status: number;
  json: unknown;
}

const ok = (json: unknown, status = 200): Reply => ({ status, json });
const fail = (status: number, error: string): Reply => ({ status, json: { error } });

export async function localApi(method: string, path: string, body: unknown): Promise<Reply> {
  try {
    return await route(method, path, body);
  } catch (error) {
    if (error instanceof z.ZodError) return fail(400, error.issues[0]?.message ?? "Invalid request");
    console.error(error);
    return fail(500, "Something went wrong in the demo backend");
  }
}

async function route(method: string, path: string, body: unknown): Promise<Reply> {
  const url = new URL(path, "http://demo.local");
  const query = Object.fromEntries(url.searchParams);
  const data = load();
  const user = data.users.find((u) => u.id === data.sessionUserId);
  const f = getFeed();
  const key = `${method} ${url.pathname}`;

  switch (key) {
    case "GET /api/meta":
      return ok({
        sampleData: true,
        sports: SPORTS.map(({ id, name }) => ({ id, name })),
        books: BOOKS,
        version: f.version,
        updatedAt: f.updatedAt,
        freeLimits: FREE_LIMITS,
      });
    case "GET /api/odds":
      return ok({ updatedAt: f.updatedAt, version: f.version, rows: buildBoard(f.listEvents(), f.listMarkets(), marketFilterSchema.parse(query)) });
    case "GET /api/ev": {
      const filter = marketFilterSchema.parse(query);
      const all = findEv(f.listEvents(), f.listMarkets(), BOOKS, { ...filter, minEv: minEvSchema.parse(query.minEv) });
      return ok({ updatedAt: f.updatedAt, version: f.version, ...gate(all, user, FREE_LIMITS.ev) });
    }
    case "GET /api/arbs": {
      const all = findArbs(f.listEvents(), f.listMarkets(), marketFilterSchema.parse(query));
      return ok({ updatedAt: f.updatedAt, version: f.version, ...gate(all, user, FREE_LIMITS.arbs) });
    }
    case "POST /api/auth/signup": {
      const { email, password } = credentialsSchema.parse(body);
      if (data.users.some((u) => u.email === email)) return fail(409, "An account with that email already exists");
      const created: User = { id: crypto.randomUUID(), email, passwordHash: await hashPassword(password), plan: "free", createdAt: new Date().toISOString() };
      save({ ...data, users: [...data.users, created], sessionUserId: created.id });
      return ok({ user: publicUser(created) }, 201);
    }
    case "POST /api/auth/login": {
      const { email, password } = credentialsSchema.parse(body);
      const found = data.users.find((u) => u.email === email);
      if (!found || !(await verifyPassword(password, found.passwordHash))) return fail(401, "Incorrect email or password");
      save({ ...data, sessionUserId: found.id });
      return ok({ user: publicUser(found) });
    }
    case "POST /api/auth/logout":
      save({ ...data, sessionUserId: null });
      return ok({ ok: true });
    case "GET /api/auth/me":
      return ok({ user: user ? publicUser(user) : null });
  }

  if (!user) return fail(401, "Sign in required");

  switch (key) {
    case "POST /api/billing/checkout":
    case "POST /api/billing/cancel": {
      const updated: User = { ...user, plan: key.endsWith("checkout") ? "pro" : "free" };
      save({ ...data, users: data.users.map((u) => (u.id === user.id ? updated : u)) });
      return ok({ user: publicUser(updated), demo: true });
    }
    case "GET /api/bets": {
      const bets = sortBets(data.bets.filter((b) => b.userId === user.id));
      return ok({ bets, stats: betStats(bets) });
    }
    case "POST /api/bets": {
      const bet: Bet = { id: crypto.randomUUID(), userId: user.id, createdAt: new Date().toISOString(), status: "open", ...newBetSchema.parse(body) };
      save({ ...data, bets: [...data.bets, bet] });
      return ok({ bet }, 201);
    }
  }

  const betMatch = /^\/api\/bets\/([^/]+)$/.exec(url.pathname);
  if (betMatch) {
    const bet = data.bets.find((b) => b.id === betMatch[1] && b.userId === user.id);
    if (!bet) return fail(404, "Bet not found");
    if (method === "PATCH") {
      const updated: Bet = { ...bet, ...betUpdateSchema.parse(body) };
      save({ ...data, bets: data.bets.map((b) => (b.id === bet.id ? updated : b)) });
      return ok({ bet: updated });
    }
    if (method === "DELETE") {
      save({ ...data, bets: data.bets.filter((b) => b.id !== bet.id) });
      return ok(null, 204);
    }
  }

  return fail(404, "Not found");
}
