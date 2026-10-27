/**
 * Where the backend lives and how to authenticate to it. One local backend (127.0.0.1:8765) serves both front ends.
 *
 * - Desktop app (Tauri): the Rust shell connects to the running backend, or starts it, and hands over its address
 *   and saved token via the `backend_connection` command.
 * - Chrome extension (workspace page): the address and token from pairing, in the extension's storage.
 * - Plain browser (Vite dev): requests go to `/api`, which the Vite proxy forwards with the token attached
 *   server-side.
 */
export interface BackendConnection {
  baseUrl: string;
  token: string | null;
}

let connection: Promise<BackendConnection> | null = null;

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** The ResumeFlow Chrome extension's storage, as far as the workspace page uses it. */
interface ExtensionChrome {
  runtime?: { id?: string };
  storage?: { local: { get(keys: string[]): Promise<Record<string, unknown>> } };
  notifications?: { create(options: Record<string, unknown>): void };
}

export function extensionChrome(): ExtensionChrome | null {
  const chrome = (globalThis as { chrome?: ExtensionChrome }).chrome;
  return typeof location !== "undefined" && location.protocol === "chrome-extension:" && chrome?.runtime?.id ? chrome : null;
}

/** v2.0: the workspace page inside the Chrome extension, using the backend address and token from pairing. */
export function isExtension(): boolean {
  return extensionChrome() !== null;
}

export const DEFAULT_BACKEND_URL = "http://127.0.0.1:8765";

async function extensionConnection(): Promise<BackendConnection> {
  const stored = (await extensionChrome()!.storage!.local.get(["baseUrl", "token"])) as { baseUrl?: string; token?: string };
  return { baseUrl: stored.baseUrl || DEFAULT_BACKEND_URL, token: stored.token ?? null };
}

export function getBackendConnection(): Promise<BackendConnection> {
  if (!connection) {
    connection = isTauri()
      ? import("@tauri-apps/api/core").then(({ invoke }) => invoke<BackendConnection>("backend_connection"))
      : isExtension()
        ? extensionConnection()
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

/** Desktop only: asks the shell to start the shared backend again if it stopped answering, and reconnects. */
export async function restartBackend(): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  const fresh = await invoke<BackendConnection>("restart_backend");
  connection = Promise.resolve(fresh);
}
