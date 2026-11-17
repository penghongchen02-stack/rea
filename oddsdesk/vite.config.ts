import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// `vite build --mode demo` produces a standalone build whose backend runs in the browser.
export default defineConfig(({ mode }) => ({
  root: "web",
  base: mode === "demo" ? "./" : "/",
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8787" } },
  build: {
    outDir: mode === "demo" ? "../dist-demo" : "../dist",
    emptyOutDir: true,
    rollupOptions: mode === "demo" ? { output: { inlineDynamicImports: true } } : {},
  },
  test: { root: ".", include: ["tests/**/*.test.ts"] },
}));
