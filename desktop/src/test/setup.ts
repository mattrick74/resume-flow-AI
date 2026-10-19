import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { resetBackendConnection } from "@/lib/backend";

// findBy*/waitFor give a busy machine longer than the default 1 s before failing.
configure({ asyncUtilTimeout: 5000 });

// jsdom lacks these browser APIs used by Radix and the theme provider.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }),
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetBackendConnection();
  localStorage.clear();
  document.documentElement.className = "";
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});
