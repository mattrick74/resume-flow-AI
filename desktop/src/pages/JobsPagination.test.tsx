import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { JobSummary } from "@/lib/types";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const job = (n: number): JobSummary => ({
  id: `j${n}`,
  job_url: `https://jobs.example.com/${n}`,
  company: "Acme",
  title: `Data Engineer ${n}`,
  location: null,
  status: "DRAFT",
  created_at: "2026-10-27T10:00:00Z",
  analyzed_at: null,
});

const ALL = Array.from({ length: 60 }, (_, i) => job(i + 1));

function setup() {
  return mockBackend({
    override: (call) => {
      if (call.method !== "GET" || call.path.split("?")[0] !== "/jobs") return undefined;
      const params = new URLSearchParams(call.path.split("?")[1] ?? "");
      const offset = Number(params.get("offset") ?? 0);
      const limit = Number(params.get("limit") ?? 100);
      return json({ items: ALL.slice(offset, offset + limit), total: ALL.length, offset, limit });
    },
  });
}

const lastJobsQuery = (calls: { method: string; path: string }[]) =>
  new URLSearchParams(calls.filter((c) => c.method === "GET" && c.path.startsWith("/jobs?")).at(-1)!.path.split("?")[1]);

describe("job list pages", () => {
  it("shows 25 jobs a page and moves between pages", async () => {
    const { calls } = setup();
    renderApp("/jobs");
    expect(await screen.findByText("1–25 of 60 jobs")).toBeInTheDocument();
    expect(screen.getByText("Data Engineer 25")).toBeInTheDocument();
    expect(screen.queryByText("Data Engineer 26")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Data Engineer 26")).toBeInTheDocument();
    expect(screen.getByText("26–50 of 60 jobs")).toBeInTheDocument();
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    expect(lastJobsQuery(calls).get("offset")).toBe("25");

    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("51–60 of 60 jobs")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("changes the page size and starts again from page 1", async () => {
    const { calls } = setup();
    renderApp("/jobs?page=2");
    expect(await screen.findByText("26–50 of 60 jobs")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(await screen.findByText("1–50 of 60 jobs")).toBeInTheDocument();
    expect(screen.getByText("Data Engineer 50")).toBeInTheDocument();
    const query = lastJobsQuery(calls);
    expect([query.get("offset"), query.get("limit")]).toEqual(["0", "50"]);
  });

  it("asks the backend for jobs needing answers, so every page holds them", async () => {
    const { calls } = setup();
    renderApp("/jobs?filter=answers");
    await screen.findByText("1–25 of 60 jobs");
    await waitFor(() => expect(lastJobsQuery(calls).get("needs_answers")).toBe("true"));
  });
});
