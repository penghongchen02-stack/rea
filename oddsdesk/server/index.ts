import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import express from "express";
import { createApp } from "./app.js";
import { SampleFeed } from "./sample-data.js";
import { JsonStore } from "./store.js";

const port = Number(process.env.PORT ?? 8787);
const production = process.env.NODE_ENV === "production";
const tickMs = Number(process.env.FEED_TICK_MS ?? 15_000);
const dataFile = process.env.DATA_FILE ?? fileURLToPath(new URL("../data/db.json", import.meta.url));
const distDir = fileURLToPath(new URL("../dist", import.meta.url));

const feed = new SampleFeed(process.env.FEED_SEED ? Number(process.env.FEED_SEED) : undefined);
const store = await JsonStore.open(dataFile);
const app = createApp({ feed, store, secureCookies: production && process.env.INSECURE_COOKIES !== "1" });

if (production && existsSync(distDir)) {
  app.use(express.static(distDir));
  app.get(/^(?!\/api\/).*/, (_req, res) => res.sendFile("index.html", { root: distDir }));
}

const timer = setInterval(() => feed.tick(), tickMs);
const server = app.listen(port, () => {
  console.log(`LineScout API on http://localhost:${port} (sample feed, tick ${tickMs}ms)`);
});

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    clearInterval(timer);
    server.close(() => process.exit(0));
  });
}
