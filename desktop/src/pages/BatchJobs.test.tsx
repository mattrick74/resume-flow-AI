import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { parseJobText } from "@/components/jobs/BatchJobForm";
import type { BatchResult, JobSummary } from "@/lib/types";
import { EMPTY_SUMMARY, json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

/** Pastes like an operator copying a job description (typing it key by key is slow). */
async function paste(field: HTMLElement, text: string) {
  await userEvent.click(field);
  await userEvent.paste(text);
}

const JD = "Data Engineer. We need Python, SQL and five years of experience building pipelines on AWS.";

function jobSummary(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: "j1",
    job_url: "https://jobs.example.com/1",
    company: "Acme",
    title: "Data Engineer",
    location: "Remote",
    status: "DRAFT",
    created_at: new Date().toISOString(),
    analyzed_at: null,
    top_candidate: null,
    eligible_count: 0,
    applications: 0,
    processing: null,
    application: null,
    ...overrides,
  };
}

describe("parseJobText", () => {
  it("takes the URL from a 'URL:' line or a bare first line", () => {
    expect(parseJobText("URL: https://a.example.com/1\n\nSenior Engineer\nPython")).toEqual({ job_url: "https://a.example.com/1", raw_jd: "Senior Engineer\nPython" });
    expect(parseJobText("https://b.example.com/2\r\nAnalyst")).toEqual({ job_url: "https://b.example.com/2", raw_jd: "Analyst" });
    expect(parseJobText("Analyst role\nNo link here")).toEqual({ job_url: "", raw_jd: "Analyst role\nNo link here" });
  });
});

describe("several jobs at once", () => {
  it("saves and queues every job, keeping duplicates for a decision", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method !== "POST" || call.path !== "/jobs/batch") return undefined;
        const body = call.body as { items: unknown[]; confirm_duplicates: boolean };
        const result: BatchResult = body.confirm_duplicates
          ? { results: [{ index: 0, status: "created", job: jobSummary({ id: "j3" }) }], queued: [], skipped: [] }
          : {
              results: [
                { index: 0, status: "created", job: jobSummary() },
                {
                  index: 1,
                  status: "duplicate",
                  code: "duplicate_job",
                  message: "This job looks like one you've already entered.",
                  details: { matches: [{ job_id: "old", title: "BI Analyst", company: "Globex", created_at: "2026-10-13T10:00:00Z", reason: "same_url", similarity: 1 }] },
                },
              ],
              queued: ["j1"],
              skipped: [],
            };
        return json(result, 201);
      },
    });
    renderApp("/jobs/new");
    await userEvent.click(await screen.findByRole("tab", { name: /several jobs/i }));
    await userEvent.type(screen.getByLabelText("Job 1 posting URL"), "https://jobs.example.com/1");
    await paste(screen.getByLabelText("Job 1 description"), JD);
    await userEvent.type(screen.getByLabelText("Job 2 posting URL"), "https://jobs.example.com/2");
    await userEvent.type(screen.getByLabelText("Job 2 company"), "Globex");
    await paste(screen.getByLabelText("Job 2 description"), JD.replace("Data Engineer", "BI Analyst"));
    await userEvent.click(screen.getByRole("button", { name: "Save & generate all" }));

    await waitFor(() => expect(calls.some((c) => c.path === "/jobs/batch")).toBe(true));
    expect(calls.find((c) => c.path === "/jobs/batch")?.body).toMatchObject({
      process: true,
      confirm_duplicates: false,
      items: [
        { job_url: "https://jobs.example.com/1", raw_jd: JD },
        { job_url: "https://jobs.example.com/2", company: "Globex" },
      ],
    });
    // The saved job leaves the form; the duplicate stays with an explanation.
    expect(await screen.findByText(/Already saved: BI Analyst · Globex \(same URL\)/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Job 2 posting URL")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Job 1 posting URL")).toHaveValue("https://jobs.example.com/2");

    await userEvent.click(screen.getByRole("button", { name: "Save & generate duplicates anyway" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/jobs/batch")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/jobs/batch")[1].body).toMatchObject({ process: true, confirm_duplicates: true, items: [{ company: "Globex" }] });
  });

  it("skips postings already in history and says which", async () => {
    const earlier = (job_id: string, title: string) => ({ matches: [{ job_id, title, company: "Globex", created_at: "2026-10-13T10:00:00Z", reason: "same_url", similarity: 1 }] });
    mockBackend({
      override: (call) =>
        call.method === "POST" && call.path === "/jobs/batch"
          ? json(
              {
                results: [
                  { index: 0, status: "created", job: jobSummary() },
                  { index: 1, status: "skipped", code: "url_in_history", message: "Already in history", details: earlier("old", "BI Analyst") },
                  { index: 2, status: "skipped", code: "url_in_history", message: "Being generated now", details: earlier("saved", "ML Engineer"), queued_job_id: "saved" },
                ],
                queued: ["j1", "saved"],
                skipped: [],
              } satisfies BatchResult,
              201,
            )
          : undefined,
    });
    renderApp("/jobs/new?mode=batch");
    await userEvent.click(await screen.findByRole("button", { name: /add another job/i }));
    for (const n of [1, 2, 3]) {
      await paste(screen.getByLabelText(`Job ${n} posting URL`), `https://jobs.example.com/${n}`);
      await paste(screen.getByLabelText(`Job ${n} description`), JD);
    }
    await userEvent.click(screen.getByRole("button", { name: "Save & generate all" }));
    // Nothing needs attention, so the form is done; the toast names the skipped posting.
    expect(await screen.findByText(/1 skipped — already in your history \(BI Analyst · Globex\)\. 1 you saved earlier is being generated now\./)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  });

  it("checks every entry before sending anything", async () => {
    const { calls } = mockBackend();
    renderApp("/jobs/new?mode=batch");
    await userEvent.type(await screen.findByLabelText("Job 1 posting URL"), "careers page");
    await userEvent.type(screen.getByLabelText("Job 2 posting URL"), "https://jobs.example.com/2");
    await userEvent.type(screen.getByLabelText("Job 2 description"), "Too short");
    await userEvent.click(screen.getByRole("button", { name: /save 2 for later/i }));
    expect(await screen.findAllByText(/Enter the full posting URL|Paste the full job description/)).toHaveLength(2);
    expect(calls.some((c) => c.path === "/jobs/batch")).toBe(false);
  });

  it("imports one job per text file", async () => {
    mockBackend();
    renderApp("/jobs/new?mode=batch");
    const input = await screen.findByLabelText("Choose job description text files");
    await userEvent.upload(input, [
      new File([`URL: https://jobs.example.com/a\n${JD}`], "a.txt", { type: "text/plain" }),
      new File([`https://jobs.example.com/b\n${JD}`], "b.txt", { type: "text/plain" }),
    ]);
    expect(await screen.findByLabelText("Job 2 posting URL")).toHaveValue("https://jobs.example.com/b");
    expect(screen.getByLabelText("Job 1 posting URL")).toHaveValue("https://jobs.example.com/a");
    expect(screen.getByLabelText("Job 1 description")).toHaveValue(JD);
    expect(screen.queryByLabelText("Job 3 posting URL")).not.toBeInTheDocument(); // the empty starter rows were replaced
  });
});

describe("one job, saved for later", () => {
  it("saves without analyzing and clears the form", async () => {
    const { calls } = mockBackend({
      override: (call) =>
        call.method === "POST" && call.path === "/jobs/batch" ? json({ results: [{ index: 0, status: "created", job: jobSummary() }], queued: [], skipped: [] }, 201) : undefined,
    });
    renderApp("/jobs/new");
    await userEvent.type(await screen.findByLabelText(/Job posting URL/), "https://jobs.example.com/1");
    await paste(screen.getByLabelText("Job description"), JD);
    await userEvent.click(screen.getByRole("button", { name: "Save for later" }));
    expect(await screen.findByText("Saved for later")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/jobs/batch")?.body).toEqual({ items: [{ job_url: "https://jobs.example.com/1", raw_jd: JD }], confirm_duplicates: false });
    expect(calls.some((c) => c.path === "/jobs" && c.method === "POST")).toBe(false);
    expect(screen.getByLabelText(/Job posting URL/)).toHaveValue("");
  });

  it("points to the earlier job when the URL is already in history", async () => {
    const { calls } = mockBackend({
      override: (call) =>
        call.method === "POST" && call.path === "/jobs"
          ? json(
              {
                error: {
                  code: "url_in_history",
                  message: "This job posting is already in your history, so it was skipped.",
                  details: { matches: [{ job_id: "old", title: "Data Engineer", company: "Acme", created_at: "2026-10-13T10:00:00Z", reason: "same_url", similarity: 1 }] },
                },
              },
              409,
            )
          : undefined,
    });
    renderApp("/jobs/new");
    await userEvent.type(await screen.findByLabelText(/Job posting URL/), "https://jobs.example.com/1");
    await paste(screen.getByLabelText("Job description"), JD);
    await userEvent.click(screen.getByRole("button", { name: /Analyze & match/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("This job posting is already in your history")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/jobs/old");
    expect(within(dialog).queryByRole("button", { name: /anyway/ })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/jobs" && c.method === "POST")).toHaveLength(1);
  });
});

describe("jobs list", () => {
  const jobs = [
    jobSummary({ id: "j1", title: "Data Engineer" }),
    jobSummary({ id: "j2", title: "BI Analyst", company: "Globex" }),
    jobSummary({ id: "j3", title: "Platform Engineer", status: "MATCHED", top_candidate: "Avery Quinlan", eligible_count: 3, application: { id: "a1", status: "AWAITING_REVIEW", candidate_id: "c1" } }),
    jobSummary({ id: "j4", title: "ML Engineer", processing: { id: "p4", status: "queued", progress: 0, error: null } }),
  ];

  const setup = () =>
    mockBackend({
      summary: { ...EMPTY_SUMMARY, saved_jobs: 2 },
      override: (call) => {
        if (call.method === "GET" && call.path.split("?")[0] === "/jobs") return json(jobs);
        if (call.method === "POST" && call.path === "/jobs/process") {
          const body = call.body as { job_ids?: string[]; all_saved?: boolean };
          return json({ queued: body.all_saved ? ["j1", "j2"] : body.job_ids, skipped: [] }, 202);
        }
        return undefined;
      },
    });

  it("generates every saved job with one click", async () => {
    const { calls } = setup();
    renderApp("/jobs");
    await userEvent.click(await screen.findByRole("button", { name: /generate all saved \(2\)/i }));
    await waitFor(() => expect(calls.some((c) => c.path === "/jobs/process")).toBe(true));
    expect(calls.find((c) => c.path === "/jobs/process")?.body).toEqual({ all_saved: true });
    expect(await screen.findByText("2 jobs queued for tailored documents")).toBeInTheDocument();
  });

  it("generates only the selected jobs and shows progress and results", async () => {
    const { calls } = setup();
    renderApp("/jobs");
    const row = (title: string) => screen.getByRole("link", { name: title }).closest("tr") as HTMLElement;
    await screen.findByText("Data Engineer");

    expect(within(row("ML Engineer")).getByText("Queued")).toBeInTheDocument();
    expect(within(row("ML Engineer")).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(row("Platform Engineer")).getByText("Awaiting review")).toBeInTheDocument();
    expect(within(row("Platform Engineer")).queryByRole("checkbox")).not.toBeInTheDocument();
    expect(within(row("Data Engineer")).getByText("Saved")).toBeInTheDocument();

    await userEvent.click(within(row("BI Analyst")).getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Generate for 1 selected" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/jobs/process")).toBe(true));
    expect(calls.find((c) => c.path === "/jobs/process")?.body).toEqual({ job_ids: ["j2"] });
  });

  it("redirects the old Matching address", async () => {
    setup();
    renderApp("/matching");
    expect(await screen.findByRole("heading", { name: "Jobs" })).toBeInTheDocument();
  });
});

describe("dashboard", () => {
  it("offers one-click generation for saved jobs", async () => {
    const { calls } = mockBackend({
      summary: { ...EMPTY_SUMMARY, saved_jobs: 3 },
      override: (call) => (call.path === "/jobs/process" ? json({ queued: ["a", "b", "c"], skipped: [] }, 202) : undefined),
    });
    renderApp("/");
    expect(await screen.findByText("3 saved jobs waiting")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Generate all" }));
    await waitFor(() => expect(calls.find((c) => c.path === "/jobs/process")?.body).toEqual({ all_saved: true }));
  });
});
