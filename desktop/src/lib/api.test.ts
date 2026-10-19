import { describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch } from "@/lib/api";
import { json, mockBackend } from "@/test/mockBackend";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async (command: string) => {
    if (command !== "backend_connection") throw new Error(`unexpected command ${command}`);
    return { baseUrl: "http://127.0.0.1:47123", token: "session-token-from-shell" };
  }),
}));

describe("apiFetch", () => {
  it("uses the Vite proxy without a token in a plain browser", async () => {
    const { fetchMock } = mockBackend();
    await apiFetch("/health");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/health");
    expect(new Headers(init?.headers).has("Authorization")).toBe(false);
  });

  it("uses the shell-provided URL and bearer token inside Tauri", async () => {
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    const { fetchMock } = mockBackend();
    await apiFetch("/settings");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:47123/settings");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer session-token-from-shell");
  });

  it("maps structured error bodies to ApiError", async () => {
    mockBackend({
      override: () =>
        json({ error: { code: "invalid_settings", message: "Settings are invalid", details: [{ loc: ["daily_goal"] }] } }, 422),
    });
    const error = await apiFetch("/settings").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "invalid_settings", message: "Settings are invalid" });
  });

  it("reports an unreachable engine as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(apiFetch("/health")).rejects.toMatchObject({ code: "network_error", status: 0 });
  });

  it("returns undefined for 204 responses and sends JSON bodies", async () => {
    const { calls } = mockBackend();
    await expect(
      apiFetch("/settings/secrets/openai_api_key", { method: "PUT", body: JSON.stringify({ value: "x" }) }),
    ).resolves.toBeUndefined();
    expect(calls[0].headers.get("Content-Type")).toBe("application/json");
  });
});
