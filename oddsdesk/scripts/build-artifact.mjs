// Inline the demo build (vite build --mode demo) into one HTML page body for hosting as a single file.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const dir = new URL("../dist-demo/", import.meta.url).pathname;
const index = readFileSync(join(dir, "index.html"), "utf8");
const asset = (pattern) => {
  const match = pattern.exec(index);
  if (!match) throw new Error(`Missing asset ${pattern} in dist-demo/index.html`);
  return readFileSync(join(dir, match[1]), "utf8");
};
const css = asset(/href="\.\/(assets\/[^"]+\.css)"/);
const js = asset(/src="\.\/(assets\/[^"]+\.js)"/).replace(/<\/(script)/gi, "<\\/$1");
const icon = /<link rel="icon"[^>]*>/.exec(index)?.[0] ?? "";

const page = `<title>LineScout</title>
${icon}
<style>
${css}
/* Hosted page: keep the sticky bar clear of the phone's status area. */
:root { color-scheme: dark; }
html, body { background: var(--bg); }
.topbar { top: env(safe-area-inset-top, 0px); }
</style>
<div id="root"></div>
<script type="module">
${js}
</script>
`;
writeFileSync(join(dir, "linescout.html"), page);
console.log(`wrote dist-demo/linescout.html (${(page.length / 1024).toFixed(0)} KB)`);
