import { QueryClient } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppProviders } from "@/App";
import { LicenseGate } from "@/components/layout/LicenseGate";
import type { LicenseStatus } from "@/lib/types";
import { json, mockBackend } from "@/test/mockBackend";

const status = (overrides: Partial<LicenseStatus> = {}): LicenseStatus => ({
  required: true,
  valid: false,
  code: "license_expired",
  message: "Your ResumeFlow license expired on Oct 27, 2026 10:00. Enter a new license key.",
  name: "Alex Doe",
  key_id: "ab12cd34",
  expires_at: "2026-10-27T10:00:00+00:00",
  days_left: 0,
  bound_to_this_pc: true,
  machine_code: "4F2A-9C1E-77B0-D3E8",
  ...overrides,
});

function renderGate() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  return render(
    <AppProviders queryClient={queryClient}>
      <LicenseGate>
        <p>The app</p>
      </LicenseGate>
    </AppProviders>,
  );
}

describe("the license key", () => {
  it("asks for a new key when the license expired, and opens the app once one is activated", async () => {
    let current = status();
    const { calls } = mockBackend({
      override: (call) => {
        if (call.path !== "/license") return undefined;
        if (call.method === "POST") current = status({ valid: true, code: "ok", message: null, days_left: 7 });
        return json(current);
      },
    });
    renderGate();
    expect(await screen.findByText(/license expired/)).toBeInTheDocument();
    expect(screen.getByText("4F2A-9C1E-77B0-D3E8")).toBeInTheDocument(); // to send with the request for a key
    expect(screen.queryByText("The app")).not.toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox", { name: "License key" }), "RF1-abc.def-ghijkl");
    await userEvent.click(screen.getByRole("button", { name: /Activate/ }));
    expect(await screen.findByText("The app")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/license")?.body).toEqual({ key: "RF1-abc.def-ghijkl" });
  });

  it("says why a key was refused and stays on the screen", async () => {
    mockBackend({
      override: (call) => {
        if (call.path !== "/license") return undefined;
        if (call.method === "POST") return json({ error: { code: "license_other_machine", message: "This license key is for another PC." } }, 422);
        return json(status({ code: "license_required", message: "ResumeFlow needs a license key." }));
      },
    });
    renderGate();
    await userEvent.type(await screen.findByRole("textbox", { name: "License key" }), "RF1-for.another-pc");
    await userEvent.click(screen.getByRole("button", { name: /Activate/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This license key is for another PC.");
    expect(screen.queryByText("The app")).not.toBeInTheDocument();
  });

  it("warns when the key ends within two days, without blocking the app", async () => {
    mockBackend({ override: (call) => (call.path === "/license" ? json(status({ valid: true, code: "ok", message: null, days_left: 1.5 })) : undefined) });
    renderGate();
    expect(await screen.findByText("The app")).toBeInTheDocument();
    expect(screen.getByText(/Your ResumeFlow license ends/)).toBeInTheDocument();
  });
});
