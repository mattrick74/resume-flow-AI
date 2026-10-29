import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ApplicationSummary } from "@/lib/types";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const application = (n: number): ApplicationSummary => ({
  id: `a${n}`,
  job_id: `j${n}`,
  candidate_id: `c${n}`,
  candidate_name: `Candidate ${n}`,
  company: "Acme",
  title: `Data Engineer ${n}`,
  job_url: `https://jobs.example.com/${n}`,
  application_date: "2026-10-29",
  sequence_number: n,
  status: n % 2 === 0 ? "SUBMITTED" : "EXPORTED",
  selection_method: "automatic",
  output_directory: null,
  pdf_status: "succeeded",
  error: null,
  created_at: "2026-10-29T10:00:00Z",
  updated_at: "2026-10-29T10:00:00Z",
  exported_at: "2026-10-29T10:00:00Z",
});

const ALL = Array.from({ length: 60 }, (_, index) => application(index + 1));

function setup() {
  return mockBackend({
    override: (call) => {
      if (call.method !== "GET" || call.path.split("?")[0] !== "/applications") return undefined;
      const params = new URLSearchParams(call.path.split("?")[1] ?? "");
      const q = (params.get("q") ?? "").toLowerCase();
      const statuses = (params.get("status") ?? "").split(",").filter(Boolean);
      const candidate = params.get("candidate_id");
      const filtered = ALL.filter(
        (app) =>
          (!q || `${app.company} ${app.title} ${app.candidate_name}`.toLowerCase().includes(q)) &&
          (!statuses.length || statuses.includes(app.status)) &&
          (!candidate || app.candidate_id === candidate),
      );
      const offset = Number(params.get("offset") ?? 0);
      const limit = Number(params.get("limit") ?? 25);
      return json({ items: filtered.slice(offset, offset + limit), total: filtered.length, offset, limit });
    },
  });
}

const lastApplicationsQuery = (calls: { method: string; path: string }[]) =>
  new URLSearchParams(calls.filter((call) => call.method === "GET" && call.path.startsWith("/applications?")).at(-1)!.path.split("?")[1]);

describe("application list pages", () => {
  it("shows 25 applications per page and moves between pages", async () => {
    const { calls } = setup();
    renderApp("/applications");

    expect(await screen.findByText("1–25 of 60 applications")).toBeInTheDocument();
    expect(screen.getByText("Data Engineer 25")).toBeInTheDocument();
    expect(screen.queryByText("Data Engineer 26")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Data Engineer 26")).toBeInTheDocument();
    expect(screen.getByText("26–50 of 60 applications")).toBeInTheDocument();
    expect(lastApplicationsQuery(calls).get("offset")).toBe("25");

    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("51–60 of 60 applications")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("changes the page size and starts again from page 1", async () => {
    const { calls } = setup();
    renderApp("/applications?page=2");

    expect(await screen.findByText("26–50 of 60 applications")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Rows per page" }), "50");
    expect(await screen.findByText("1–50 of 60 applications")).toBeInTheDocument();
    const query = lastApplicationsQuery(calls);
    expect([query.get("offset"), query.get("limit")]).toEqual(["0", "50"]);
  });

  it("returns to page 1 when a filter changes", async () => {
    const { calls } = setup();
    renderApp("/applications?page=2");

    expect(await screen.findByText("26–50 of 60 applications")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Status"), "SUBMITTED");
    expect(await screen.findByText("1–25 of 30 applications")).toBeInTheDocument();
    await waitFor(() => {
      const query = lastApplicationsQuery(calls);
      expect([query.get("status"), query.get("offset")]).toEqual(["SUBMITTED", "0"]);
    });
  });
});
