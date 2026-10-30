import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { JobSummary } from "@/lib/types";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const JOB: JobSummary = {
  id: "j1",
  job_url: "https://jobs.example.com/1",
  company: "Acme",
  title: "Data Engineer",
  location: "Austin, TX",
  status: "DRAFT",
  created_at: "2026-10-27T10:00:00Z",
  analyzed_at: null,
  applications: 0,
};

function setup(job: JobSummary = JOB, deleteResponse?: Response) {
  let present = true;
  const backend = mockBackend({
    override: (call) => {
      if (call.method === "GET" && call.path.split("?")[0] === "/jobs") {
        const items = present ? [job] : [];
        return json({ items, total: items.length, offset: 0, limit: 25 });
      }
      if (call.method === "DELETE" && call.path === `/jobs/${job.id}`) {
        if (deleteResponse) return deleteResponse;
        present = false;
        return new Response(null, { status: 204 });
      }
      return undefined;
    },
  });
  renderApp("/jobs");
  return backend;
}

describe("deleting jobs from the workspace", () => {
  it("requires confirmation and can be cancelled", async () => {
    const { calls } = setup();
    await userEvent.click(await screen.findByRole("button", { name: "Delete Data Engineer" }));

    const dialog = screen.getByRole("dialog", { name: "Delete Data Engineer?" });
    expect(within(dialog).getByText(/permanently removes the saved job and its matching history/i)).toBeInTheDocument();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);

    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete Data Engineer?" })).not.toBeInTheDocument());
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  });

  it("deletes the job and refreshes the workspace", async () => {
    const { calls } = setup();
    await userEvent.click(await screen.findByRole("button", { name: "Delete Data Engineer" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete job" }));

    await waitFor(() => expect(calls.filter((call) => call.method === "DELETE" && call.path === "/jobs/j1")).toHaveLength(1));
    expect(await screen.findByText("No jobs yet")).toBeInTheDocument();
    expect(await screen.findByText("Job deleted")).toBeInTheDocument();
  });

  it("protects jobs that have application history", async () => {
    const { calls } = setup({ ...JOB, applications: 1 });
    const button = await screen.findByRole("button", { name: "Delete Data Engineer" });

    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Jobs with generated applications are kept to preserve their application history and documents.");
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
  });

  it("keeps the job visible when the backend rejects deletion", async () => {
    setup(
      JOB,
      json({ error: { code: "job_has_applications", message: "This job has applications and can't be deleted." } }, 409),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Delete Data Engineer" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete job" }));

    expect(await screen.findByText("Couldn't delete job")).toBeInTheDocument();
    expect(await screen.findByText("This job has applications and can't be deleted.")).toBeInTheDocument();
    expect(screen.getByText("Data Engineer", { selector: "a" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Delete Data Engineer?" })).toBeInTheDocument();
  });
});
