import { randomBytes, scrypt as scryptCb, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";
import type { NextFunction, Request, Response } from "express";
import type { User } from "./types.js";

const scrypt = promisify(scryptCb) as (password: string, salt: Buffer, keylen: number) => Promise<Buffer>;

export const SESSION_COOKIE = "ls_session";
const SESSION_TTL_MS = 7 * 24 * 3_600_000;

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const key = await scrypt(password, salt, 64);
  return `scrypt$${salt.toString("hex")}$${key.toString("hex")}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, saltHex, keyHex] = stored.split("$");
  if (scheme !== "scrypt" || !saltHex || !keyHex) return false;
  const expected = Buffer.from(keyHex, "hex");
  const actual = await scrypt(password, Buffer.from(saltHex, "hex"), expected.length);
  return timingSafeEqual(actual, expected);
}

/** In-memory sessions; users sign in again after a server restart. */
export class Sessions {
  private readonly sessions = new Map<string, { userId: string; expires: number }>();

  create(userId: string): string {
    const token = randomBytes(32).toString("base64url");
    this.sessions.set(token, { userId, expires: Date.now() + SESSION_TTL_MS });
    return token;
  }

  resolve(token: string | undefined): string | null {
    if (!token) return null;
    const session = this.sessions.get(token);
    if (!session) return null;
    if (session.expires < Date.now()) {
      this.sessions.delete(token);
      return null;
    }
    return session.userId;
  }

  destroy(token: string | undefined): void {
    if (token) this.sessions.delete(token);
  }
}

export function readCookie(req: Request, name: string): string | undefined {
  for (const part of (req.headers.cookie ?? "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

export function setSessionCookie(res: Response, token: string, secure: boolean): void {
  res.cookie(SESSION_COOKIE, token, { httpOnly: true, sameSite: "lax", secure, maxAge: SESSION_TTL_MS, path: "/" });
}

declare module "express-serve-static-core" {
  interface Request {
    user?: User;
  }
}

export function requireUser(req: Request, res: Response, next: NextFunction): void {
  if (!req.user) {
    res.status(401).json({ error: "Sign in required" });
    return;
  }
  next();
}
