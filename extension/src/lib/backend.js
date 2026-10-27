// The local backend, as the service worker calls it (requirements 3.2: only the service worker holds the token).
// Extension pages reach these through chrome.runtime messages handled in background.js.

export const DEFAULT_BACKEND_URL = "http://127.0.0.1:8765";

export class BackendError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function connection() {
  const { baseUrl, token } = await chrome.storage.local.get(["baseUrl", "token"]);
  return { baseUrl: (baseUrl || DEFAULT_BACKEND_URL).replace(/\/+$/, ""), token: token || null };
}

async function request(path, init = {}, { auth = true } = {}) {
  const { baseUrl, token } = await connection();
  const headers = new Headers(init.headers || {});
  if (auth && token) headers.set("Authorization", `Bearer ${token}`);
  if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  } catch {
    throw new BackendError(0, "backend_unreachable", "The ResumeFlow backend isn't running. Start it (ResumeFlow Backend) and try again.");
  }
  return response;
}

async function errorFrom(response) {
  const body = await response.json().catch(() => null);
  const error = body?.error;
  return new BackendError(response.status, error?.code ?? `http_${response.status}`, error?.message ?? response.statusText, error?.details);
}

/** JSON request; returns the parsed body (undefined for 204). */
export async function api(method, path, body, options) {
  const response = await request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }, options);
  if (response.status === 204) return undefined;
  if (!response.ok) throw await errorFrom(response);
  return response.json();
}

/** A file from the backend as base64, its name and type (for attaching to forms or downloading). */
export async function fetchFile(path) {
  const response = await request(path, { method: "GET" });
  if (!response.ok) throw await errorFrom(response);
  const buffer = await response.arrayBuffer();
  const disposition = response.headers.get("content-disposition") || "";
  const filename = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] || "file";
  return { base64: toBase64(new Uint8Array(buffer)), filename: decodeURIComponent(filename), mime: response.headers.get("content-type") || "application/octet-stream" };
}

/** Multipart upload (the captured confirmation screen). */
export async function upload(path, formData) {
  const response = await request(path, { method: "POST", body: formData });
  if (!response.ok) throw await errorFrom(response);
  return response.json();
}

/** Connection state for the side panel and options page: running, version, paired. */
export async function status() {
  let health;
  try {
    health = await api("GET", "/health", undefined, { auth: false });
  } catch (error) {
    return { running: false, paired: false, message: error.message };
  }
  const { token } = await connection();
  if (!token) return { running: true, paired: false, version: health.version, message: "Pair the extension with the backend (Options)." };
  try {
    await api("GET", "/settings");
    return { running: true, paired: true, version: health.version };
  } catch (error) {
    if (error.status === 401) return { running: true, paired: false, version: health.version, message: "The saved token was not accepted. Pair again (Options)." };
    return { running: true, paired: false, version: health.version, message: error.message };
  }
}

export async function pair(code, baseUrl) {
  if (baseUrl) await chrome.storage.local.set({ baseUrl: baseUrl.replace(/\/+$/, "") });
  const { token } = await api("POST", "/pair", { code: String(code).trim() }, { auth: false });
  await chrome.storage.local.set({ token });
  return status();
}

export async function saveToken(token, baseUrl) {
  await chrome.storage.local.set({ token: token.trim(), ...(baseUrl ? { baseUrl: baseUrl.replace(/\/+$/, "") } : {}) });
  return status();
}

export function toBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(binary);
}
