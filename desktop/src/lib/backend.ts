/**
 * Where the sidecar lives and how to authenticate to it.
 *
 * - Inside Tauri the Rust shell started the sidecar on a random loopback port
 *   with a per-session token and hands both over via the `backend_connection`
 *   command.
 * - In a plain browser (Vite dev), requests go to `/api`, which the Vite proxy
 *   forwards with the token attached server-side.
 */
export interface BackendConnection {
  baseUrl: string;
  token: string | null;
}

let connection: Promise<BackendConnection> | null = null;

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function getBackendConnection(): Promise<BackendConnection> {
  if (!connection) {
    connection = isTauri()
      ? import("@tauri-apps/api/core").then(({ invoke }) => invoke<BackendConnection>("backend_connection"))
      : Promise.resolve({ baseUrl: "/api", token: null });
    // Do not cache a failed lookup; the next call retries.
    connection.catch(() => {
      connection = null;
    });
  }
  return connection;
}

export function resetBackendConnection() {
  connection = null;
}

/** Desktop only: asks the shell to start a fresh sidecar (new port, same session token). */
export async function restartBackend(): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  const fresh = await invoke<BackendConnection>("restart_backend");
  connection = Promise.resolve(fresh);
}
