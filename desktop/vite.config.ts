import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Browser-only development: the Vite server proxies /api to a locally running
// sidecar and injects the session token server-side, so the token never
// reaches page JavaScript. Inside Tauri the shell supplies URL and token instead.
const backendUrl = process.env.RESUMEFLOW_BACKEND_URL ?? "http://127.0.0.1:8765";
const sessionToken = process.env.RESUMEFLOW_SESSION_TOKEN;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420, // must match tauri.conf.json devUrl
    strictPort: true,
    proxy: {
      "/api": {
        target: backendUrl,
        rewrite: (p) => p.replace(/^\/api/, ""),
        headers: sessionToken ? { Authorization: `Bearer ${sessionToken}` } : undefined,
      },
    },
  },
  // Served from local disk inside the desktop shell, so one larger bundle is fine.
  build: { target: "es2022", chunkSizeWarningLimit: 1024 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // Typing-heavy UI tests can take several seconds when every test file runs in parallel on a busy machine.
    testTimeout: 20_000,
  },
});
