import { QueryClient } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppProviders, createQueryClient } from "@/App";
import { NAV_ITEMS } from "@/app/navigation";
import { BackendGate } from "@/components/layout/BackendGate";
import { EMPTY_SUMMARY, json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

describe("app shell", () => {
  it("does not reload stale workspace data when the extension regains focus", () => {
    expect(createQueryClient().getDefaultOptions().queries?.refetchOnWindowFocus).toBe(false);
  });

  it("renders the live dashboard", async () => {
    mockBackend({ summary: { ...EMPTY_SUMMARY, generated_today: 37, awaiting_review: 4 } });
    renderApp("/");
    expect(await screen.findByText("37")).toBeInTheDocument();
    expect(screen.getByText("of 100 applications generated")).toBeInTheDocument();
    expect(screen.getByText("No applications yet")).toBeInTheDocument();
  });

  it("shows recent activity with status badges", async () => {
    mockBackend({
      summary: {
        ...EMPTY_SUMMARY,
        recent_activity: [
          {
            application_id: "a1",
            candidate_name: "Joseph Hein",
            company: "Acme",
            title: "Data Engineer",
            status: "AWAITING_REVIEW",
            updated_at: new Date().toISOString(),
          },
        ],
      },
    });
    renderApp("/");
    expect(await screen.findByText("Joseph Hein")).toBeInTheDocument();
    expect(screen.getByText("Data Engineer · Acme")).toBeInTheDocument();
    expect(screen.getAllByText("Preparing files").length).toBeGreaterThan(0);
  });

  it("offers a retry when the dashboard cannot load", async () => {
    mockBackend({
      override: (call) =>
        call.path === "/dashboard/summary"
          ? json({ error: { code: "internal_error", message: "An unexpected error occurred" } }, 500)
          : undefined,
    });
    renderApp("/");
    expect(await screen.findByText("Couldn’t load this view")).toBeInTheDocument();
    expect(screen.getByText("An unexpected error occurred")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it.each(NAV_ITEMS.slice(1))("navigates to $label from the sidebar", async (item) => {
    mockBackend();
    renderApp("/");
    const nav = screen.getByRole("navigation", { name: "Main" });
    await userEvent.click(within(nav).getByRole("link", { name: new RegExp(item.label) }));
    expect(await screen.findByRole("heading", { level: 1, name: item.label })).toBeInTheDocument();
  });

  it("supports Alt+number shortcuts", async () => {
    mockBackend();
    renderApp("/");
    fireEvent.keyDown(window, { altKey: true, code: "Digit4", key: "4" }); // Dashboard, New Job, Jobs, Candidates
    expect(await screen.findByRole("heading", { level: 1, name: "Candidates" })).toBeInTheDocument();
  });

  it("opens the command palette with Ctrl+K and runs a command", async () => {
    mockBackend();
    renderApp("/");
    fireEvent.keyDown(window, { ctrlKey: true, key: "k" });
    const input = await screen.findByRole("textbox", { name: "Search commands" });
    await userEvent.type(input, "analytics{Enter}");
    expect(await screen.findByRole("heading", { level: 1, name: "Analytics" })).toBeInTheDocument();
  });

  it("collapses the sidebar with Ctrl+B", async () => {
    mockBackend();
    renderApp("/");
    fireEvent.keyDown(window, { ctrlKey: true, key: "b" });
    expect(await screen.findByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
  });

  it("shows a not-found page for unknown routes", async () => {
    mockBackend();
    renderApp("/nowhere");
    expect(await screen.findByText("Page not found")).toBeInTheDocument();
  });
});

describe("backend gate", () => {
  const renderGate = () =>
    render(
      <AppProviders queryClient={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <BackendGate>
          <p>app content</p>
        </BackendGate>
      </AppProviders>,
    );

  it("waits for the engine before showing the app", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    renderGate();
    expect(await screen.findByText("Starting ResumeFlow engine…")).toBeInTheDocument();
    expect(screen.queryByText("app content")).not.toBeInTheDocument();
  });

  it("shows the app once /health succeeds", async () => {
    mockBackend();
    renderGate();
    await waitFor(() => expect(screen.getByText("app content")).toBeInTheDocument());
  });
});
