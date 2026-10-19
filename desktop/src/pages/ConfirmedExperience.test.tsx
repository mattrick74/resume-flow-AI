import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { BatchGaps, CoverageView, EvidenceOverview, ExperienceRecord, JobSummary, SkillGapItem, SkillGapReport } from "@/lib/types";
import { applicationDetail, candidateDetail, jobDetail, match } from "@/test/fixtures";
import { EMPTY_SUMMARY, json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const ROLE = "Data Engineer at Northwind (2020-01 – present)";

function item(skill: string, status: SkillGapItem["status"], kind: SkillGapItem["kind"], evidence: string | null = null): SkillGapItem {
  const open = status === "plausible" || status === "unknown";
  return { skill, normalized: skill.toLowerCase(), kind, status, match: status === "matched" ? "exact" : evidence ? "partial" : "missing", evidence, answer: null, role_ids: [], needs_answer: open, pending_suggestion: false };
}

function gapReport(overrides: Partial<SkillGapReport> = {}): SkillGapReport {
  return {
    job_id: "j1",
    job_title: "Data Engineer",
    company: "Acme",
    candidate_id: "c1",
    candidate_name: "Avery Quinlan",
    items: [item("Python", "matched", "required", "Built Python pipelines"), item("Kubernetes", "unknown", "required"), item("GraphQL", "unknown", "required"), item("Redis", "plausible", "preferred", "Built a caching layer")],
    hard_gates: [],
    counts: { matched: 1, confirmed: 0, plausible: 1, unknown: 2, rejected: 0 },
    unanswered: 3,
    pending_suggestions: 0,
    score: 72,
    potential_score: 90,
    previous_score: null,
    roles: [{ id: "r1", label: ROLE }],
    ...overrides,
  };
}

function record(overrides: Partial<ExperienceRecord> = {}): ExperienceRecord {
  return {
    id: "x1",
    role_id: "r1",
    role: ROLE,
    role_found: true,
    skills: ["Kubernetes"],
    context: "deployed the pipelines on Kubernetes",
    project: null,
    outcome: null,
    narrative: "Deployed the pipelines on Kubernetes.",
    status: "suggested",
    origin: "gap",
    drafted_by: "offline",
    issues: [],
    issue_counts: { error: 0, warning: 0 },
    source_job_ids: ["j1"],
    created_at: "2026-10-18T09:00:00Z",
    updated_at: "2026-10-18T09:00:00Z",
    approved_at: null,
    ...overrides,
  };
}

describe("missing job skills on a job", () => {
  it("asks Have used / Have not used / Unsure, drafts a bullet from the description and approves it", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/jobs/j1") return json(jobDetail({ matches: [match({ score: 80, potential_score: 90, previous_score: 70 })] }));
        if (call.path.startsWith("/jobs/j1/skill-gaps")) return json(gapReport());
        if (call.path === "/candidates/c1/skills/confirm-bulk")
          return json({ answers: [], suggestions: [record()], resumed: [], rematched: ["j1"], candidate: candidateDetail() });
        if (call.path === "/candidates/c1/experience-evidence/approve") return json({ records: [record({ status: "approved" })], resumed: [], rematched: ["j1"] });
        return undefined;
      },
    });
    renderApp("/jobs/j1");
    await userEvent.click(await screen.findByRole("button", { name: /3 job skills not in Avery's profile/ })); // optional, collapsed
    const list = await screen.findByRole("list", { name: "Missing job skills" });
    expect(within(list).queryByText("Python")).not.toBeInTheDocument(); // already shown in a role
    expect(screen.getByText("potential 90")).toBeInTheDocument(); // the match card: shown, never used to choose
    expect(screen.getByLabelText("Change since the last ranking")).toHaveTextContent("▲ from 70");

    await userEvent.click(screen.getByRole("button", { name: "Have used: Kubernetes" }));
    await userEvent.click(screen.getByRole("checkbox", { name: `Kubernetes at ${ROLE}` }));
    await userEvent.type(screen.getByRole("textbox", { name: "What was done with Kubernetes" }), "deployed the pipelines on Kubernetes");
    await userEvent.click(screen.getByRole("button", { name: "Have not used: GraphQL" }));
    await userEvent.click(screen.getByRole("button", { name: "Mark the rest Unsure" }));
    await userEvent.click(screen.getByRole("button", { name: /Save 3 answers/ }));

    await waitFor(() => expect(calls.some((c) => c.path === "/candidates/c1/skills/confirm-bulk")).toBe(true));
    expect(calls.find((c) => c.path === "/candidates/c1/skills/confirm-bulk")?.body).toEqual({
      source_job_ids: ["j1"],
      answers: [
        { skill: "Kubernetes", decision: "used", role_ids: ["r1"], context: "deployed the pipelines on Kubernetes", project: null, outcome: null },
        { skill: "GraphQL", decision: "not_used", role_ids: [], context: null, project: null, outcome: null },
        { skill: "Redis", decision: "unsure", role_ids: [], context: null, project: null, outcome: null },
      ],
    });

    expect(await screen.findByRole("textbox", { name: `Bullet for ${ROLE}` })).toHaveValue("Deployed the pipelines on Kubernetes.");
    expect(screen.getByText(/you said: “deployed the pipelines on Kubernetes”/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Approve 1 bullet/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/candidates/c1/experience-evidence/approve")).toBe(true));
    expect(calls.find((c) => c.path === "/candidates/c1/experience-evidence/approve")?.body).toEqual({ ids: ["x1"], acknowledge: false, edits: {} });
  });

  it("asks again before approving bullets that say more than the description", async () => {
    let attempts = 0;
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/jobs/j1") return json(jobDetail());
        if (call.path.startsWith("/jobs/j1/skill-gaps")) return json(gapReport({ items: [item("Kubernetes", "unknown", "required")] }));
        if (call.path === "/candidates/c1/skills/confirm-bulk") {
          const flagged = record({ narrative: "Led 6 engineers on Kubernetes.", issues: [{ severity: "error", code: "unsupported_leadership", message: "Claims leadership or ownership that the description doesn't state" }], issue_counts: { error: 1, warning: 0 } });
          return json({ answers: [], suggestions: [flagged], resumed: [], rematched: [], candidate: candidateDetail() });
        }
        if (call.path === "/candidates/c1/experience-evidence/approve") {
          attempts += 1;
          return attempts === 1
            ? json({ error: { code: "unresolved_issues", message: "1 suggestion(s) say something the description doesn't support." } }, 409)
            : json({ records: [], resumed: [], rematched: [] });
        }
        return undefined;
      },
    });
    renderApp("/jobs/j1");
    await userEvent.click(await screen.findByRole("button", { name: /1 job skill not in Avery's profile/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Have used: Kubernetes" }));
    await userEvent.click(screen.getByRole("checkbox", { name: `Kubernetes at ${ROLE}` }));
    await userEvent.type(screen.getByRole("textbox", { name: "What was done with Kubernetes" }), "deployed the pipelines");
    await userEvent.click(screen.getByRole("button", { name: /Save 1 answer/ }));
    expect(await screen.findByText(/Claims leadership or ownership/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Approve 1 bullet/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Approve anyway" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/candidates/c1/experience-evidence/approve")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/candidates/c1/experience-evidence/approve")[1].body).toMatchObject({ acknowledge: true });
  });
});

describe("one confirmation session for every waiting job", () => {
  it("lists the most required skills across jobs and continues the jobs after answering", async () => {
    const waiting = (id: string): JobSummary => ({
      id,
      job_url: `https://jobs.example.com/${id}`,
      company: "Acme",
      title: `Platform Engineer ${id}`,
      location: null,
      status: "MATCHED",
      created_at: "2026-10-18T09:00:00Z",
      analyzed_at: "2026-10-18T09:00:00Z",
      top_candidate: "Avery Quinlan",
      eligible_count: 1,
      applications: 0,
      processing: { id: `p-${id}`, status: "succeeded", progress: 100, error: null },
      application: null,
      needs_confirmation: true,
    });
    const gaps: BatchGaps = {
      jobs: 2,
      skipped: [],
      candidates: [
        {
          candidate_id: "c1",
          candidate_name: "Avery Quinlan",
          roles: [{ id: "r1", label: ROLE }],
          job_ids: ["j1", "j2"],
          skills: [
            { skill: "Kubernetes", normalized: "kubernetes", required_count: 2, preferred_count: 0, impact: 11.7, jobs: [], answer: null, status: "unknown", evidence: null, needs_answer: true },
            { skill: "Redis", normalized: "redis", required_count: 0, preferred_count: 1, impact: 3.3, jobs: [], answer: null, status: "unknown", evidence: null, needs_answer: true },
          ],
        },
      ],
    };
    const { calls } = mockBackend({
      summary: { ...EMPTY_SUMMARY, jobs_needing_confirmation: 2 },
      override: (call) => {
        if (call.method === "GET" && call.path.split("?")[0] === "/jobs") return json([waiting("j1"), waiting("j2")]);
        if (call.path === "/jobs/skill-gaps/batch") return json(gaps);
        if (call.path === "/candidates/c1/skills/confirm-bulk") return json({ answers: [{ skill: "Kubernetes", normalized: "kubernetes", decision: "used", issues: [] }], suggestions: [], resumed: ["j1", "j2"], rematched: [], candidate: candidateDetail() });
        return undefined;
      },
    });
    renderApp("/jobs");
    expect(await screen.findAllByRole("button", { name: /Needs answers/ })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Answer missing skills (2)" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("required by 2 jobs")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Have used: Kubernetes" }));
    await userEvent.click(within(dialog).getByRole("button", { name: /Save 1 answer/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/candidates/c1/skills/confirm-bulk")).toBe(true));
    expect(calls.find((c) => c.path === "/candidates/c1/skills/confirm-bulk")?.body).toEqual({
      source_job_ids: ["j1", "j2"],
      answers: [{ skill: "Kubernetes", decision: "used", role_ids: [], context: null, project: null, outcome: null }],
    });
    expect(await screen.findByText(/2 waiting jobs continue now/)).toBeInTheDocument();
  });
});

describe("candidate evidence", () => {
  const overview: EvidenceOverview = {
    profile_revision: 3,
    records: [record(), record({ id: "x2", status: "approved", narrative: "Ran Spark jobs on Kubernetes.", approved_at: "2026-10-18T09:30:00Z" })],
    history: [{ skill: "Kubernetes", normalized: "kubernetes", decision: "used", roles: [ROLE], years: null, proficiency: null, last_used: null, note: null, decided_at: "2026-10-18T09:00:00Z", jobs: [{ id: "j1", title: "Data Engineer", company: "Acme" }] }],
    roles: [{ id: "r1", label: ROLE }],
  };

  it("shows suggestions, approved additions and history, and drafts a bullet from the interview", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/candidates/c1") return json(candidateDetail({ consent_confirmed_at: "2026-10-18T08:00:00Z" }));
        if (call.method === "GET" && call.path === "/candidates/c1/experience-evidence") return json(overview);
        if (call.method === "POST" && call.path === "/candidates/c1/experience-evidence") return json(overview, 201);
        if (call.method === "PATCH" && call.path === "/candidates/c1/experience-evidence/x2") return json({ records: [], resumed: [], rematched: [], overview });
        return undefined;
      },
    });
    renderApp("/candidates/c1?tab=evidence");
    expect(await screen.findByText("Suggested bullets (1)")).toBeInTheDocument();
    expect(screen.getByText("Ran Spark jobs on Kubernetes.")).toBeInTheDocument();
    const history = screen.getByRole("table");
    expect(within(history).getByText("Have used")).toBeInTheDocument();
    expect(within(history).getByText("Data Engineer · Acme")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Technologies used there"), "Kafka");
    await userEvent.type(screen.getByLabelText("What did they build or improve?"), "built order-event processing");
    await userEvent.click(screen.getByRole("button", { name: /Add bullet/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/candidates/c1/experience-evidence")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.path === "/candidates/c1/experience-evidence")?.body).toEqual({
      role_id: "r1",
      skills: ["Kafka"],
      context: "built order-event processing",
      project: null,
      outcome: null,
      origin: "interview",
    });

    await userEvent.click(screen.getByRole("button", { name: `Undo approval for ${ROLE}` }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ status: "suggested" }));
  });
});

describe("application coverage and regeneration", () => {
  const view: CoverageView = {
    profile_revision: 1,
    current_profile_revision: 2,
    coverage: {
      rows: [],
      required: { total: 5, in_resume: 4, confirmed: 4, in_original_resume: 2, missing: ["GraphQL"] },
      preferred: { total: 2, in_resume: 1, confirmed: 1, in_original_resume: 1, missing: ["Redis"] },
      keyword_coverage_pct: 71,
      confirmed_coverage_pct: 71,
      original_coverage_pct: 43,
      unsupported_claims: 0,
      duplicates: [],
      suggestions: ["Answer whether the candidate has used GraphQL; confirmed skills can then be added."],
      note: "Coverage of this job description's skills. It is not an applicant tracking system score.",
    },
    changes: {
      skills: { added: ["Kubernetes"], kept: ["Python"], not_used: [] },
      bullets: [{ role: "Data Engineer at Northwind", text: "Ran Spark jobs on Kubernetes.", kind: "added", original: null, addition_ids: ["x2"] }],
      not_used_bullets: [],
      previous: null,
    },
  };

  it("reports coverage before and after, and regenerates with newly approved additions", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/applications/a1") return json(applicationDetail({ profile_revision: 1, current_profile_revision: 2 }));
        if (call.path === "/applications/a1/coverage") return json(view);
        if (call.path === "/applications/a1/approve-additions") return json({ records: [], resumed: [], rematched: [], regenerating: true, application: applicationDetail({ status: "GENERATING" }) }, 202);
        return undefined;
      },
    });
    renderApp("/applications/a1");
    await userEvent.click(await screen.findByRole("button", { name: /Regenerate with approved additions/ }));
    await waitFor(() => expect(calls.find((c) => c.path === "/applications/a1/approve-additions")?.body).toEqual({ record_ids: [], regenerate: true }));

    await userEvent.click(screen.getByRole("tab", { name: /Coverage/ }));
    const before = await screen.findByLabelText("Coverage before and after");
    expect(within(before).getByText("43%")).toBeInTheDocument();
    expect(within(before).getByText("71%")).toBeInTheDocument();
    expect(screen.getByText(/not an applicant tracking system score/)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Suggested improvements" })).toHaveTextContent("GraphQL");
    expect(screen.getByText("Ran Spark jobs on Kubernetes.")).toBeInTheDocument();
    expect(screen.getByText("Skills added:").parentElement).toHaveTextContent("Kubernetes");
  });
});

describe("instant by default", () => {
  it("adds bullets from the description at once, without an approval step", async () => {
    mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/jobs/j1") return json(jobDetail());
        if (call.path.startsWith("/jobs/j1/skill-gaps")) return json(gapReport({ items: [item("Kubernetes", "unknown", "required")] }));
        if (call.path === "/candidates/c1/skills/confirm-bulk")
          return json({ answers: [{ skill: "Kubernetes", normalized: "kubernetes", decision: "used", issues: [] }], approved: [record({ status: "approved" })], suggestions: [], resumed: [], rematched: ["j1"], candidate: candidateDetail() });
        return undefined;
      },
    });
    renderApp("/jobs/j1");
    expect(await screen.findByRole("button", { name: /Generate for Avery/ })).toBeEnabled(); // generating never waits for answers
    await userEvent.click(await screen.findByRole("button", { name: /1 job skill not in Avery's profile/ }));
    await userEvent.click(screen.getByRole("button", { name: "Have used: Kubernetes" }));
    await userEvent.click(screen.getByRole("checkbox", { name: `Kubernetes at ${ROLE}` }));
    await userEvent.type(screen.getByRole("textbox", { name: "What was done with Kubernetes" }), "deployed the pipelines");
    await userEvent.click(screen.getByRole("button", { name: /Save 1 answer/ }));
    expect(await screen.findByText(/1 bullet added from your descriptions/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve 1 bullet/ })).not.toBeInTheDocument();
  });
});

describe("technologies per employer", () => {
  it("show under each role in the resume preview", async () => {
    const app = applicationDetail();
    const resume = app.resume!;
    resume.content.experience[0].technologies = ["Kubernetes", "SQL Server"];
    resume.issues = [];
    resume.issue_counts = { error: 0, warning: 0, info: 0 };
    mockBackend({ override: (call) => (call.method === "GET" && call.path === "/applications/a1" ? json(app) : undefined) });
    renderApp("/applications/a1");
    expect(await screen.findByLabelText("Technologies at Northwind")).toHaveTextContent("Technologies: Kubernetes, SQL Server");
  });
});

describe("hard requirements", () => {
  it("are hidden unless the check is turned on in Settings", async () => {
    mockBackend({ override: (call) => (call.method === "GET" && call.path === "/jobs/j1" ? json(jobDetail()) : undefined) });
    renderApp("/jobs/j1");
    expect(await screen.findByLabelText("Min. years")).toBeInTheDocument(); // the requirements editor is there
    expect(screen.queryByText(/Hard requirements/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Security clearance")).not.toBeInTheDocument();
  });

  it("can be turned back on", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const toggle = await screen.findByRole("switch", { name: "Check hard requirements" });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { hard_requirement_gates?: boolean }).hard_requirement_gates === true)).toBe(true));
  });
});

describe("settings", () => {
  it("can ask before generating, require approval, and change bullets per role", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const toggle = await screen.findByRole("switch", { name: "Ask about missing job skills before generating" });
    expect(toggle).not.toBeChecked(); // off by default: generation never waits
    await userEvent.click(toggle);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { gap_confirmation?: string }).gap_confirmation === "ask")).toBe(true));
    const approval = screen.getByRole("switch", { name: "Add bullets from my descriptions without approval" });
    expect(approval).toBeChecked();
    await userEvent.click(approval);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { auto_approve_bullets?: boolean }).auto_approve_bullets === false)).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Bullets per role"), "detailed");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { bullet_density?: string }).bullet_density === "detailed")).toBe(true));
  });
});
