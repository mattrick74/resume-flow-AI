import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { applicationDetail, candidateDetail, jobDetail } from "@/test/fixtures";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

describe("completing a candidate review", () => {
  it("lists unverified education and verifies it with one click", async () => {
    const candidate = candidateDetail({
      profile: {
        ...candidateDetail().profile,
        summary: "Data engineer.",
        summary_verified: true,
        education: [{ id: "e1", institution: "Georgia Institute of Technology", degree: "Bachelor of Science in Computer Science", field: null, end_year: 2020, verified: false }],
      },
    });
    const { calls } = mockBackend({
      settings: { require_verification: true },
      override: (call) => {
        if (call.path === "/candidates/c1" && call.method === "GET") return json(candidate);
        if (call.path === "/candidates/c1" && call.method === "PATCH") return json(candidate);
        if (call.path === "/candidates/c1/review-complete") return json({ ...candidate, profile_reviewed_at: "2026-10-17T12:00:00Z" });
        return undefined;
      },
    });
    renderApp("/candidates/c1");
    await userEvent.click(await screen.findByRole("button", { name: /Mark review complete/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Bachelor of Science in Computer Science — Georgia Institute of Technology \(2020\)/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /Verify these and complete/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/candidates/c1/review-complete")).toBe(true));
    const patch = calls.find((c) => c.path === "/candidates/c1" && c.method === "PATCH")?.body as { profile: { education: { verified: boolean }[] } };
    expect(patch.profile.education[0].verified).toBe(true);
  });
});

describe("profiles without verification (the default)", () => {
  it("shows no ticks or review step, and adds a pasted list of skills", async () => {
    const candidate = candidateDetail();
    const { calls } = mockBackend({
      override: (call) => {
        if (call.path === "/candidates/c1" && call.method === "GET") return json(candidate);
        if (call.path === "/candidates/c1/skills/bulk") return json({ added: ["Kubernetes", "Terraform"], already: ["Python"], candidate }, 201);
        return undefined;
      },
    });
    renderApp("/candidates/c1");
    expect(await screen.findByRole("heading", { name: candidate.full_name })).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.path === "/settings")).toBe(true));
    expect(screen.queryByRole("button", { name: /Mark review complete/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Role details verified")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Bullet verified")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Skills/ }));
    await userEvent.type(await screen.findByLabelText(/Add skills/), "Kubernetes, Terraform{enter}Python");
    await userEvent.click(screen.getByRole("button", { name: "Add skills" }));
    expect(await screen.findByText("2 added · already listed: Python")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/candidates/c1/skills/bulk")?.body).toEqual({ text: "Kubernetes, Terraform\nPython" });
  });
});

describe("instructions for the AI writer", () => {
  it("are sent when generating from a job", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.path === "/jobs/j1" && call.method === "GET") return json(jobDetail());
        if (call.path === "/applications" && call.method === "POST") return json(applicationDetail(), 201);
        if (call.path === "/applications/a1") return json(applicationDetail());
        return undefined;
      },
    });
    renderApp("/jobs/j1");
    await userEvent.type(await screen.findByLabelText(/Instructions for the AI/), "Lead with the .NET work.");
    await userEvent.click(screen.getByRole("button", { name: /Generate for/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/applications" && c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.path === "/applications" && c.method === "POST")?.body).toMatchObject({ instructions: "Lead with the .NET work." });
  });

  it("can be changed when regenerating", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.path === "/applications/a1" && call.method === "GET") return json(applicationDetail({ instructions: "Keep it short." }));
        if (call.path === "/applications/a1/generate") return json(applicationDetail({ status: "GENERATING" }));
        return undefined;
      },
    });
    renderApp("/applications/a1");
    await userEvent.click(await screen.findByRole("button", { name: /Regenerate/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Both documents/ }));
    const dialog = await screen.findByRole("dialog");
    const box = within(dialog).getByLabelText(/Instructions for the AI/);
    expect(box).toHaveValue("Keep it short.");
    await userEvent.clear(box);
    await userEvent.type(box, "Emphasise Azure.");
    await userEvent.click(within(dialog).getByRole("button", { name: /Regenerate/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/applications/a1/generate")).toBe(true));
    expect(calls.find((c) => c.path === "/applications/a1/generate")?.body).toEqual({ document: "both", section: "all", instructions: "Emphasise Azure." });
  });

  it("shows why an application was held instead of exported", async () => {
    mockBackend({
      override: (call) =>
        call.path === "/applications/a1"
          ? json(applicationDetail({ status: "AWAITING_REVIEW", error: "Not exported automatically: the checks found errors. Review them, then approve and export." }))
          : undefined,
    });
    renderApp("/applications/a1");
    expect(await screen.findByText(/Not exported automatically/)).toBeInTheDocument();
  });
});

describe("settings", () => {
  it("chooses what happens after generating and saves default instructions", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    await userEvent.selectOptions(await screen.findByLabelText("After generating"), "review");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { export_mode?: string }).export_mode === "review")).toBe(true));
    await userEvent.selectOptions(screen.getByLabelText("Files to save"), "pdf_docx");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { output_formats?: string }).output_formats === "pdf_docx")).toBe(true));
    await userEvent.type(screen.getByLabelText("Instructions for the AI writer"), "Use US spelling.");
    await userEvent.click(screen.getByRole("button", { name: "Save instructions" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { writing_instructions?: string }).writing_instructions === "Use US spelling.")).toBe(true));
  });

  it("can require verification again", async () => {
    const { calls } = mockBackend();
    renderApp("/settings");
    const toggle = await screen.findByRole("switch", { name: "Require verification of candidate details" });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && (c.body as { require_verification?: boolean }).require_verification === true)).toBe(true));
  });

  it("does not offer a setting that skips duplicate job URLs", async () => {
    mockBackend();
    renderApp("/settings");
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Skip job URLs already in history" })).not.toBeInTheDocument();
  });
});
