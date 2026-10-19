import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DEFAULT_TEMPLATE_STYLE } from "@/lib/templateStyle";
import type { DocumentTemplate } from "@/lib/types";
import { applicationDetail, candidateDetail } from "@/test/fixtures";
import { json, mockBackend, templatesResponse } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

const GREEN: DocumentTemplate = {
  key: "custom:7b0c6c1e-1111-4222-8333-944455556666",
  id: "7b0c6c1e-1111-4222-8333-944455556666",
  name: "Jamie Doe style",
  builtin: false,
  style: { ...DEFAULT_TEMPLATE_STYLE, font: "Georgia", heading_font: "Georgia", name_color: "2E6B30", heading_color: "2E6B30", name_align: "center", heading_rule: false },
  source_filename: "Jamie_Doe_Resume.docx",
  notes: ["The font “Georgia” isn't installed on this PC."],
  created_at: "2026-10-14T12:00:00Z",
};

const templatesSection = async () => {
  const list = await screen.findByRole("list", { name: "Document templates" });
  return list;
};

describe("document templates", () => {
  it("shows built-in and copied templates and switches the one in use", async () => {
    const { calls } = mockBackend({
      override: (call) => (call.method === "GET" && call.path === "/templates" ? json(templatesResponse("professional", [GREEN])) : undefined),
    });
    renderApp("/settings");
    const list = await templatesSection();
    const cards = within(list).getAllByRole("listitem");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual(["Professional", "Compact", "Jamie Doe style"]);
    expect(within(cards[0]).getByText("In use")).toBeInTheDocument();
    expect(within(cards[2]).getByText(/From Jamie_Doe_Resume\.docx · Georgia 10\.5pt/)).toBeInTheDocument();

    await userEvent.click(within(cards[2]).getByRole("button", { name: "Use this template" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === "/settings")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH" && c.path === "/settings")?.body).toEqual({ resume_template: GREEN.key });
  });

  it("copies the style of a chosen resume file and flags what to check", async () => {
    const { calls } = mockBackend({
      override: (call) => (call.method === "POST" && call.path === "/templates/import" ? json(GREEN, 201) : undefined),
    });
    renderApp("/settings");
    await templatesSection();
    const file = new File(["docx bytes"], "Jamie_Doe_Resume.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    await userEvent.upload(screen.getByLabelText("Choose a resume to copy the style from"), file);
    await waitFor(() => expect(calls.some((c) => c.path === "/templates/import")).toBe(true));
    expect(await screen.findByText("Check “Jamie Doe style”")).toBeInTheDocument();
    expect(screen.getByText(/Georgia” isn't installed/)).toBeInTheDocument();
  });

  it("adjusts a copied style and saves it", async () => {
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/templates") return json(templatesResponse("professional", [GREEN]));
        if (call.method === "PATCH" && call.path === `/templates/${GREEN.id}`) return json({ ...GREEN, ...(call.body as object) });
        return undefined;
      },
    });
    renderApp("/settings");
    const list = await templatesSection();
    const card = within(list).getAllByRole("listitem")[2];
    await userEvent.click(within(card).getByRole("button", { name: "More actions for Jamie Doe style" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Adjust style/ }));
    const dialog = await screen.findByRole("dialog");
    const name = within(dialog).getByLabelText("Template name");
    await userEvent.clear(name);
    await userEvent.type(name, "Green serif");
    await userEvent.click(within(dialog).getByLabelText("Line under headings"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Save template" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === `/templates/${GREEN.id}`)).toBe(true));
    expect(calls.find((c) => c.path === `/templates/${GREEN.id}`)?.body).toEqual({ name: "Green serif", style: { ...GREEN.style, heading_rule: true } });
  });

  it("copies the style of a candidate's stored resume", async () => {
    const version = {
      id: "v1",
      version: 1,
      original_filename: "Avery_Quinlan_Resume.docx",
      original_file_path: "C:\\Data\\originals\\c1\\v1_Avery_Quinlan_Resume.docx",
      mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      file_sha256: "abc",
      verification_status: "verified" as const,
      extraction_source: "heuristic" as const,
      extraction_warnings: [],
      created_at: "2026-10-14T10:00:00Z",
    };
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/candidates/c1") return json(candidateDetail({ resume_versions: [version] }));
        if (call.method === "POST" && call.path === "/templates/from-resume") return json({ ...GREEN, name: "Avery Quinlan style", notes: [] }, 201);
        return undefined;
      },
    });
    renderApp("/candidates/c1");
    await userEvent.click(await screen.findByRole("tab", { name: /Resume versions/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Use as template" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/templates/from-resume")).toBe(true));
    expect(calls.find((c) => c.path === "/templates/from-resume")?.body).toEqual({ candidate_id: "c1", version_id: "v1" });
    expect(await screen.findByText("Saved the style as “Avery Quinlan style”")).toBeInTheDocument();
  });

  it("previews documents in the template in use", async () => {
    mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/templates") return json(templatesResponse(GREEN.key, [GREEN]));
        if (call.path === "/applications/a1") return json(applicationDetail());
        return undefined;
      },
    });
    renderApp("/applications/a1");
    const name = (await screen.findAllByText("Avery Quinlan")).find((el) => el.style.color);
    await waitFor(() => expect(name?.style.color).toBe("rgb(46, 107, 48)"));
    expect(name?.style.fontFamily).toContain("Georgia");
    expect(name?.parentElement?.style.textAlign).toBe("center");
  });
});
