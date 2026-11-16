import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import type { Bet, User } from "./types.js";

interface Data {
  users: User[];
  bets: Bet[];
}

/** Small JSON-file database. Writes are serialised and atomic (temp file + rename). */
export class JsonStore {
  private data: Data = { users: [], bets: [] };
  private writing: Promise<void> = Promise.resolve();

  private constructor(private readonly path: string | null) {}

  /** Pass null for an in-memory store (tests). */
  static async open(path: string | null): Promise<JsonStore> {
    const store = new JsonStore(path);
    if (path) {
      try {
        store.data = JSON.parse(await readFile(path, "utf8")) as Data;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      }
    }
    return store;
  }

  get users(): readonly User[] {
    return this.data.users;
  }

  get bets(): readonly Bet[] {
    return this.data.bets;
  }

  async mutate(change: (data: Data) => void): Promise<void> {
    change(this.data);
    const path = this.path;
    if (!path) return;
    const snapshot = JSON.stringify(this.data, null, 2);
    this.writing = this.writing.then(async () => {
      await mkdir(dirname(path), { recursive: true });
      const tmp = `${path}.tmp`;
      await writeFile(tmp, snapshot, "utf8");
      await rename(tmp, path);
    });
    await this.writing;
  }
}
