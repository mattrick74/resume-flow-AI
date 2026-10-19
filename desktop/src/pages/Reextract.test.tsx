import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { CandidateListItem, ReextractResult, ResumeVersionInfo } from "@/lib/types";
import { candidateDetail } from "@/test/fixtures";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const version: ResumeVersionInfo = {
  id: "v1",
  version: 1,
  original_filename: "Austen.pdf",
  original_file_path: "C:\\Data\\originals\\c1\\v1_Austen.pdf",
  mime_type: "application/pdf",
  file_sha256: "abc",
  verification_status: "verified",
  extraction_source: "heuristic",
  extraction_warnings: [],
  created_at: "2026-10-14T10:00:00Z",
};

const result = (overrides: Partial<ReextractResult> = {}): ReextractResult => ({
  candidate: candidateDetail({ resume_versions: [version] }),
  extraction_source: "ai",
  warnings: [],
  roles: 3,
  bullets: 18,
  education: 1,
  skills_added: 4,
  skills_removed: 0,
  kept_verified: 2,
  ...overrides,
});

describe("re-reading resumes", () => {
  it("re-reads one stored resume after confirming", async () => {
    const { calls } = mockBackend({
      settings: { require_verification: true },
      override: (call) => {
        if (call.method === "GET" && call.path === "/candidates/c1") return json(candidateDetail({ resume_versions: [version] }));
        if (call.method === "POST" && call.path === "/candidates/c1/resumes/v1/reextract") return json(result());
        return undefined;
      },
    });
    renderApp("/candidates/c1");
    await userEvent.click(await screen.findByRole("tab", { name: /Resume versions/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Re-read" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Facts you already verified keep their tick/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /Re-read resume/ }));
    await waitFor(() => expect(calls.some((c) => c.path === "/candidates/c1/resumes/v1/reextract")).toBe(true));
    expect(await screen.findByText("Read with AI: 3 roles, 18 bullets, 1 education")).toBeInTheDocument();
  });

  it("re-reads every candidate in the list and shows progress", async () => {
    const listItem = (id: string, full_name: string): CandidateListItem => ({
      id,
      full_name,
      email: null,
      phone: null,
      location: null,
      status: "active",
      latest_version: 1,
      skills: { verified: 0, unverified: 0, rejected: 0 },
      reviewed: false,
      consent_confirmed: false,
      applications: 0,
      readiness: { verified_roles: 0, verified_skills: 0, ready_for_matching: false, blockers: [], warnings: [] },
      updated_at: "2026-10-14T10:00:00Z",
    });
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path.split("?")[0] === "/candidates") return json([listItem("c1", "Austen Schunk"), listItem("c2", "Daniel Lepkofker")]);
        const detail = /^\/candidates\/(c\d)$/.exec(call.path);
        if (call.method === "GET" && detail) return json(candidateDetail({ id: detail[1], resume_versions: [{ ...version, id: `v-${detail[1]}` }] }));
        if (call.method === "POST" && call.path === "/candidates/c2/resumes/v-c2/reextract") return json({ error: { code: "ai_failed", message: "OpenAI is busy" } }, 502);
        if (call.method === "POST" && call.path.endsWith("/reextract")) return json(result());
        return undefined;
      },
    });
    renderApp("/candidates");
    await userEvent.click(await screen.findByRole("button", { name: /Re-read all resumes/ }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /Re-read 2 resumes/ }));
    const progress = await within(dialog).findByRole("list", { name: "Re-read progress" });
    await waitFor(() => expect(within(progress).getByText("OpenAI is busy")).toBeInTheDocument());
    expect(within(progress).getByText(/Read with AI: 3 roles/)).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith("/reextract")).map((c) => c.path)).toEqual(["/candidates/c1/resumes/v-c1/reextract", "/candidates/c2/resumes/v-c2/reextract"]);
    expect(within(dialog).getByRole("button", { name: "Done" })).toBeEnabled();
  });

  it("lists what will be left out of resumes", async () => {
    mockBackend({
      override: (call) =>
        call.method === "GET" && call.path === "/candidates/c1"
          ? json(
              candidateDetail({
                readiness: {
                  verified_roles: 1,
                  verified_skills: 77,
                  ready_for_matching: true,
                  blockers: [],
                  warnings: ["Education isn't verified, so it won't appear on resumes", "2 role(s) aren't verified and won't appear on resumes"],
                },
              }),
            )
          : undefined,
    });
    renderApp("/candidates/c1");
    const list = await screen.findByRole("list", { name: "Left out of resumes" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Education isn't verified, so it won't appear on resumes",
      "2 role(s) aren't verified and won't appear on resumes",
    ]);
  });
});
