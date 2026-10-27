import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { candidateDetail } from "@/test/fixtures";
import { json, mockBackend } from "@/test/mockBackend";
import { renderApp } from "@/test/render";

describe("notes for application questions", () => {
  it("saves the candidate's notes for answering application questions", async () => {
    let notes = "Open to relocating to Austin.";
    const { calls } = mockBackend({
      override: (call) => {
        if (call.method === "GET" && call.path === "/candidates/c1") return json(candidateDetail({ answer_notes: notes }));
        if (call.method === "PATCH" && call.path === "/candidates/c1") {
          notes = ((call.body as { answer_notes?: string }).answer_notes ?? notes).trim();
          return json(candidateDetail({ answer_notes: notes }));
        }
        return undefined;
      },
    });
    renderApp("/candidates/c1?tab=answer-notes");
    const box = await screen.findByRole("textbox", { name: "Notes for application questions" });
    expect(box).toHaveValue("Open to relocating to Austin.");
    expect(screen.getByText(/never added to resumes or cover letters/)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save notes" });
    expect(save).toBeDisabled();

    await userEvent.type(box, " Salary: 150k base.");
    await userEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === "/candidates/c1")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ answer_notes: "Open to relocating to Austin. Salary: 150k base." });
    await waitFor(() => expect(screen.getByRole("button", { name: "Save notes" })).toBeDisabled());
  });
});
