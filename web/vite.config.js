import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app goes inside the Python package, so it's installed along with the
// backend and nobody installing the app needs Node.js. It's committed to git:
// after changing anything in web/, run `npm --prefix web run build` and commit the result.
const OUT_DIR = "../backend/fs_telemetry/web";

// Everything the build is made from. Must match SOURCES in backend/tests/test_web_build.py.
const SOURCES = ["index.html", "package-lock.json", "vite.config.js", "src", "public"];

// Fingerprint of the sources: SHA-256 over each file's path and contents, in path order.
// Written next to the build, so a test can tell if the build is older than the sources.
function sourceHash(root) {
  const files = [];
  const walk = (p) => {
    try {
      for (const e of readdirSync(p, { withFileTypes: true })) walk(join(p, e.name));
    } catch {
      files.push(p); // not a folder: a file
    }
  };
  for (const s of SOURCES) walk(join(root, s));
  const hash = createHash("sha256");
  const rel = (f) => relative(root, f).split(sep).join("/");
  for (const f of files.sort((a, b) => (rel(a) < rel(b) ? -1 : 1))) {
    // Line endings don't count (Git may check files out with either)
    const text = readFileSync(f).toString("latin1").replaceAll("\r\n", "\n");
    hash.update(rel(f) + "\n").update(Buffer.from(text, "latin1")).update("\n");
  }
  return hash.digest("hex");
}

const stampBuild = {
  name: "stamp-build",
  apply: "build",
  closeBundle() {
    writeFileSync(join(OUT_DIR, "build-stamp.txt"), sourceHash(".") + "\n");
  },
};

// While developing, Vite serves the React app on port 5173 and forwards
// every /api request to the FastAPI backend on port 8000, so the app talks
// to the same API it will use once it's built and served by FastAPI.
export default defineConfig({
  plugins: [react(), stampBuild],
  build: { outDir: OUT_DIR, emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
    // Check files on a timer: Windows' change notifications sometimes miss quick
    // edits, which left Vite serving an old copy of a file
    watch: { usePolling: true, interval: 500, ignored: ["**/dist/**"] },
  },
});
