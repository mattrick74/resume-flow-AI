import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { buildPatch } from "@/pages/SettingsPage";
import { DEFAULT_SETTINGS, json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const draftFrom = (overrides: Record<string, string | boolean> = {}) => ({
  output_root: DEFAULT_SETTINGS.output_root,
  analysis_model: DEFAULT_SETTINGS.analysis_model,
  writing_model: DEFAULT_SETTINGS.writing_model,
  daily_goal: "100",
  worker_concurrency: "2",
  backup_retention_count: "14",
  daily_budget_usd: "",
  monthly_budget_usd: "",
  auto_fetch_job_urls: false,
  ...overrides,
});

describe("buildPatch", () => {
  it("returns nothing when nothing changed", () => {
    expect(buildPatch(draftFrom(), DEFAULT_SETTINGS)).toEqual({ patch: {}, errors: {} });
  });

  it("converts numbers and treats empty budgets as no limit", () => {
    const saved = { ...DEFAULT_SETTINGS, daily_budget_usd: 20 };
    const { patch } = buildPatch(draftFrom({ daily_goal: "120", daily_budget_usd: "" }), saved);
    expect(patch).toEqual({ daily_goal: 120, daily_budget_usd: null });
  });

  it("flags invalid input instead of sending it", () => {
    const { errors } = buildPatch(draftFrom({ daily_goal: "", monthly_budget_usd: "-5", writing_model: " " }), DEFAULT_SETTINGS);
    expect(Object.keys(errors).sort()).toEqual(["daily_goal", "monthly_budget_usd", "writing_model"]);
  });
});

describe("settings page", () => {
  it("saves only the changed fields", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const goal = await screen.findByLabelText("Daily goal");
    await userEvent.clear(goal);
    await userEvent.type(goal, "120");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ daily_goal: 120 });
    await waitFor(() => expect(screen.queryByText("You have unsaved changes")).not.toBeInTheDocument());
  });

  it("does not send invalid values and explains why", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const goal = await screen.findByLabelText("Daily goal");
    await userEvent.clear(goal);
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect(await screen.findByText("Enter a whole number")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("shows server validation errors next to the field", async () => {
    mockBackend({
      override: (call) =>
        call.method === "PATCH"
          ? json(
              {
                error: {
                  code: "invalid_settings",
                  message: "Settings are invalid",
                  details: [{ loc: ["worker_concurrency"], msg: "Input should be less than or equal to 8", type: "less_than_equal" }],
                },
              },
              422,
            )
          : undefined,
    });
    renderApp("/settings");
    const workers = await screen.findByLabelText("Concurrent workers");
    await userEvent.clear(workers);
    await userEvent.type(workers, "50");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));
    expect(await screen.findByText("Input should be less than or equal to 8")).toBeInTheDocument();
    expect(workers).toHaveAttribute("aria-invalid", "true");
  });

  it("stores the API key through the secrets endpoint, never via settings", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const keyInput = await screen.findByLabelText("OpenAI API key");
    await userEvent.type(keyInput, "sk-test-1234567890");
    await userEvent.click(screen.getByRole("button", { name: "Save key" }));

    await waitFor(() => expect(screen.getByText("Stored securely")).toBeInTheDocument());
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/settings/secrets/openai_api_key");
    expect(put?.body).toEqual({ value: "sk-test-1234567890" });
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });

  it("applies and persists the theme immediately", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const group = await screen.findByRole("radiogroup", { name: "Theme" });
    await userEvent.click(within(group).getByRole("radio", { name: /dark/i }));
    expect(document.documentElement).toHaveClass("dark");
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ theme: "dark" }));
  });
});
