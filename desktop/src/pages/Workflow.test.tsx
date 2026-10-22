import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { applicationDetail, candidateDetail, jobDetail } from "@/test/fixtures";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

describe("candidate import", () => {
  it("uploads chosen resumes and reports progress", async () => {
    const { calls } = mockBackend({
      override: (call) =>
        call.method === "POST" && call.path === "/candidates/import"
          ? json({ candidate: candidateDetail(), created: true, version: 1, skills_added: 4, extraction_source: "heuristic", warnings: [] }, 201)
          : undefined,
    });
    renderApp("/candidates");
    const input = await screen.findByLabelText("Choose resume files");
    const resume = new File(["docx bytes"], "Avery_Quinlan_Resume.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    const notes = new File(["hello"], "notes.txt", { type: "text/plain" });
    await userEvent.upload(input, [resume, notes], { applyAccept: false });

    expect(await screen.findByText(/Import finished/)).toBeInTheDocument();
    expect(screen.getByText("Only .docx and .pdf files can be imported")).toBeInTheDocument();
    expect(screen.getByText(/Avery Quinlan · offline extraction/)).toBeInTheDocument();
    const uploads = calls.filter((c) => c.method === "POST" && c.path === "/candidates/import");
    expect(uploads).toHaveLength(1); // the .txt file never reached the backend
  });
});

describe("new job", () => {
  it("warns about duplicates and lets the operator confirm", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method !== "POST" || call.path !== "/jobs") return undefined;
        const body = call.body as { confirm_duplicate?: boolean };
        if (!body.confirm_duplicate) {
          return json(
            {
              error: {
                code: "duplicate_job",
                message: "This job looks like one you've already entered.",
                details: { matches: [{ job_id: "old", title: "Data Engineer", company: "Acme", created_at: "2026-10-13T10:00:00Z", reason: "same_url", similarity: 1 }] },
              },
            },
            409,
          );
        }
        return json(jobDetail(), 201);
      },
    });
    renderApp("/jobs/new");
    await userEvent.type(await screen.findByLabelText(/Job posting URL/), "https://jobs.example.com/1");
    await userEvent.type(screen.getByLabelText("Job description"), "Data Engineer. We need Python, SQL and five years of experience building pipelines on AWS.");
    await userEvent.click(screen.getByRole("button", { name: /analyze & match/i }));

    expect(await screen.findByText("This job may already be in ResumeFlow")).toBeInTheDocument();
    expect(screen.getByText(/Same posting URL/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create anyway" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/jobs" && c.method === "POST")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/jobs" && c.method === "POST")[1].body).toMatchObject({ confirm_duplicate: true });
  });

  it("validates the URL and description before calling the engine", async () => {
    const { calls } = mockBackend();
    renderApp("/jobs/new");
    await userEvent.type(await screen.findByLabelText(/Job posting URL/), "not a url");
    await userEvent.click(screen.getByRole("button", { name: /analyze & match/i }));
    expect(await screen.findByText(/Enter the full posting URL/)).toBeInTheDocument();
    expect(screen.getByText("Paste the full job description")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/jobs" && c.method === "POST")).toBe(false);
  });

  it("accepts a job description without a posting URL", async () => {
    const { calls } = mockBackend();
    renderApp("/jobs/new");
    await userEvent.type(screen.getByLabelText("Job description"), "Data Engineer. We need Python, SQL and five years of experience building pipelines on AWS.");
    await userEvent.click(screen.getByRole("button", { name: /analyze & match/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/jobs" && c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.path === "/jobs" && c.method === "POST")?.body).not.toHaveProperty("job_url");
  });
});

describe("matching", () => {
  it("requires a reason before choosing someone other than the top match", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.path === "/jobs/j1" && call.method === "GET") return json(jobDetail());
        if (call.path === "/applications" && call.method === "POST") return json(applicationDetail(), 201);
        if (call.path === "/applications/a1") return json(applicationDetail());
        return undefined;
      },
    });
    renderApp("/jobs/j1");
    expect(await screen.findByText("Recommended")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Choose instead…" }));
    const confirm = await screen.findByRole("button", { name: "Choose and generate" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Client asked for Theo");
    await userEvent.click(confirm);
    await waitFor(() => expect(calls.some((c) => c.path === "/applications" && c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.path === "/applications" && c.method === "POST")?.body).toEqual({
      job_id: "j1",
      candidate_id: "c2",
      override_reason: "Client asked for Theo",
      instructions: null,
    });
  });
});

describe("review", () => {
  it("has no checking or improve step and approves in one click", async () => {
    let approved: unknown = null;
    mockBackend({
      override: (call) => {
        if (call.path === "/applications/a1" && call.method === "GET") return json(applicationDetail());
        if (call.path === "/applications/a1/approve") {
          approved = call.body;
          return json(applicationDetail({ status: "APPROVED" }));
        }
        return undefined;
      },
    });
    renderApp("/applications/a1");
    expect(await screen.findByText("Built pipelines for 250 clients")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Checks/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Improve/ })).not.toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Progress" }));
    expect(steps.queryByText("Review")).not.toBeInTheDocument();
    expect(steps.queryByText("Approved")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(approved).toEqual({ acknowledge_warnings: true }));
  });
});
