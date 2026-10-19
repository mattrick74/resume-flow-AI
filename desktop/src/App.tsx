import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router";

import { ThemeProvider, ThemeSync } from "@/app/theme";
import { AppShell } from "@/components/layout/AppShell";
import { BackendGate } from "@/components/layout/BackendGate";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AnalyticsPage } from "@/pages/AnalyticsPage";
import { ApplicationPage } from "@/pages/ApplicationPage";
import { ApplicationsPage } from "@/pages/ApplicationsPage";
import { CandidateDetailPage } from "@/pages/CandidateDetailPage";
import { CandidatesPage } from "@/pages/CandidatesPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { JobPage } from "@/pages/JobPage";
import { JobsPage } from "@/pages/JobsPage";
import { NewJobPage } from "@/pages/NewJobPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ReviewQueuePage } from "@/pages/ReviewQueuePage";
import { SettingsPage } from "@/pages/SettingsPage";

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 5_000, retry: 1 },
      mutations: { retry: 0 },
    },
  });
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<DashboardPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/new" element={<NewJobPage />} />
        <Route path="jobs/:id" element={<JobPage />} />
        <Route path="matching" element={<Navigate to="/jobs" replace />} />
        <Route path="review" element={<ReviewQueuePage />} />
        <Route path="candidates" element={<CandidatesPage />} />
        <Route path="candidates/:id" element={<CandidateDetailPage />} />
        <Route path="applications" element={<ApplicationsPage />} />
        <Route path="applications/:id" element={<ApplicationPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export function AppProviders({ queryClient, children }: { queryClient: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={300}>
          {children}
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}

export function App() {
  const [queryClient] = useState(createQueryClient);
  return (
    <AppProviders queryClient={queryClient}>
      <BackendGate>
        <ThemeSync />
        {/* Hash routing: the packaged app is served from a custom protocol with no server-side routes. */}
        <HashRouter>
          <AppRoutes />
        </HashRouter>
      </BackendGate>
    </AppProviders>
  );
}
