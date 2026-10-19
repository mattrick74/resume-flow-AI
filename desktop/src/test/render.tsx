import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { AppProviders, AppRoutes } from "@/App";
import { ThemeSync } from "@/app/theme";

export function renderApp(path = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false }, mutations: { retry: 0 } },
  });
  return render(
    <AppProviders queryClient={queryClient}>
      <ThemeSync />
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}
