// Browser-only development without Rust: runs the sidecar and the Vite UI together.
//
//   node scripts/dev-browser.mjs        then open http://127.0.0.1:1420
//
// A fresh session token is generated for each run and given to both processes;
// the Vite proxy attaches it server-side, so page JavaScript never sees it.
import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// 8767: clear of the shared backend (8765) and the debug desktop app's backend (8766).
const port = process.env.RESUMEFLOW_PORT ?? "8767";
const python = process.env.RESUMEFLOW_PYTHON ?? path.join(root, "backend", ".venv", "Scripts", "python.exe");

const env = {
  ...process.env,
  RESUMEFLOW_SESSION_TOKEN: randomBytes(32).toString("base64url"),
  RESUMEFLOW_BACKEND_URL: `http://127.0.0.1:${port}`,
  RESUMEFLOW_DATA_DIR: process.env.RESUMEFLOW_DATA_DIR ?? path.join(root, ".dev-data"),
};

const backend = spawn(python, ["-m", "resumeflow", "--port", port, "--parent-pid", String(process.pid)], {
  cwd: path.join(root, "backend"),
  env,
  stdio: "inherit",
});
const ui = spawn(process.execPath, [path.join(root, "desktop", "node_modules", "vite", "bin", "vite.js")], {
  cwd: path.join(root, "desktop"),
  env,
  stdio: "inherit",
});

function shutdown(code = 0) {
  backend.kill();
  ui.kill();
  process.exit(code);
}

backend.on("error", (error) => {
  console.error(`[dev] could not start the backend with ${python}: ${error.message}`);
  shutdown(1);
});
backend.on("exit", (code) => console.error(`[dev] backend exited with code ${code}`));
ui.on("exit", (code) => shutdown(code ?? 0));
process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
