import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type JobListParams } from "@/lib/api";
import { DEFAULT_TEMPLATE_STYLE } from "@/lib/templateStyle";
import type { AppSettings, ApplicationDetail, CandidateDetail, JobDetail, JobSummary, PipelineStatus, TemplateStyle } from "@/lib/types";

export const queryKeys = {
  health: ["health"] as const,
  settings: ["settings"] as const,
  dashboard: ["dashboard", "summary"] as const,
  candidates: ["candidates"] as const,
  candidate: (id: string) => ["candidates", id] as const,
  jobs: ["jobs"] as const,
  job: (id: string) => ["jobs", id] as const,
  applications: ["applications"] as const,
  application: (id: string) => ["applications", id] as const,
  analytics: (days: number) => ["analytics", days] as const,
  backups: ["backups"] as const,
  pdfConverter: ["pdf-converter"] as const,
  processingEvents: ["processing-jobs", "events"] as const,
  templates: ["templates"] as const,
};

const IN_PROGRESS: PipelineStatus[] = ["GENERATING", "VALIDATING"];

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: api.health,
    retry: false,
    // Poll quickly while the engine is starting, slowly once it is up.
    refetchInterval: (query) => (query.state.status === "success" ? 10_000 : 1_000),
    refetchIntervalInBackground: true,
  });
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: api.settings.get });
}

/** Settings → "Require verification". Off (the default): profiles are used as entered, so no ticks or review step are shown. */
export function useVerificationRequired() {
  return useSettings().data?.settings.require_verification ?? false;
}

/** Settings → hard requirement checks. Off (the default): no gates are shown or applied. */
export function useHardRequirementGates() {
  return useSettings().data?.settings.hard_requirement_gates ?? false;
}

export function useUpdateSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (changes: Partial<AppSettings>) => api.settings.update(changes),
    onSuccess: (data) => {
      client.setQueryData(queryKeys.settings, data);
      void client.invalidateQueries({ queryKey: queryKeys.dashboard });
      void client.invalidateQueries({ queryKey: queryKeys.templates });
    },
  });
}

// ---------------------------------------------------------------- document templates

export function useTemplates() {
  return useQuery({ queryKey: queryKeys.templates, queryFn: api.templates.list, staleTime: 30_000 });
}

/** The style documents are exported with; previews use it so review matches the files. */
export function useSelectedTemplateStyle(): TemplateStyle {
  const templates = useTemplates();
  const data = templates.data;
  return data?.templates.find((t) => t.key === data.selected)?.style ?? DEFAULT_TEMPLATE_STYLE;
}

export function useSecretMutations(name: string) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: queryKeys.settings });
  return {
    store: useMutation({ mutationFn: (value: string) => api.settings.storeSecret(name, value), onSuccess: refresh }),
    remove: useMutation({ mutationFn: () => api.settings.deleteSecret(name), onSuccess: refresh }),
  };
}

export function useDashboardSummary() {
  return useQuery({ queryKey: queryKeys.dashboard, queryFn: api.dashboard.summary, refetchInterval: 15_000, refetchIntervalInBackground: true });
}

// ---------------------------------------------------------------- candidates

export function useCandidates(params: { status?: string; q?: string } = {}) {
  return useQuery({ queryKey: [...queryKeys.candidates, params], queryFn: () => api.candidates.list(params) });
}

export function useCandidate(id: string) {
  return useQuery({ queryKey: queryKeys.candidate(id), queryFn: () => api.candidates.get(id) });
}

/** Mutations returning a fresh CandidateDetail write it straight into the cache. */
export function useCandidateMutation<V>(id: string, fn: (vars: V) => Promise<CandidateDetail>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (detail) => {
      client.setQueryData(queryKeys.candidate(id), detail);
      void client.invalidateQueries({ queryKey: queryKeys.candidates, exact: false, refetchType: "inactive" });
      void client.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

// ---------------------------------------------------------------- jobs

const BUSY_APPLICATION = new Set<PipelineStatus>(["GENERATING", "VALIDATING"]);

/** True while any job is queued, being processed, or its documents are still being written. */
export function jobsInFlight(jobs: JobSummary[] | undefined) {
  return (jobs ?? []).some(
    (job) =>
      job.status === "ANALYZING" ||
      job.processing?.status === "queued" ||
      job.processing?.status === "running" ||
      (job.application ? BUSY_APPLICATION.has(job.application.status) : false),
  );
}

/** One page of the job list; the previous page stays on screen while the next one loads. */
export function useJobs(params: JobListParams = {}) {
  return useQuery({
    queryKey: [...queryKeys.jobs, params],
    queryFn: () => api.jobs.list(params),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (jobsInFlight(query.state.data?.items) ? 2_000 : false),
    refetchIntervalInBackground: true,
  });
}

/** Queues saved (or failed) jobs for the full pipeline: analyze, match, create the application, generate. */
export function useProcessJobs() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.jobs.process,
    onSettled: () => {
      void client.invalidateQueries({ queryKey: queryKeys.jobs });
      void client.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: queryKeys.job(id),
    queryFn: () => api.jobs.get(id),
    // Poll while the background pipeline is working on this job.
    refetchInterval: (query) => {
      const job = query.state.data;
      if (!job) return false;
      const busy =
        job.status === "ANALYZING" ||
        job.processing?.status === "queued" ||
        job.processing?.status === "running" ||
        job.applications.some((a) => BUSY_APPLICATION.has(a.status));
      return busy ? 2_000 : false;
    },
    refetchIntervalInBackground: true,
  });
}

export function useJobMutation<V>(id: string, fn: (vars: V) => Promise<JobDetail>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (detail) => {
      client.setQueryData(queryKeys.job(id), detail);
      void client.invalidateQueries({ queryKey: queryKeys.jobs, exact: false, refetchType: "inactive" });
    },
  });
}

// ---------------------------------------------------------------- applications

export function useApplications(params: Parameters<typeof api.applications.list>[0] = {}, options: { poll?: boolean } = {}) {
  return useQuery({
    queryKey: [...queryKeys.applications, params],
    queryFn: () => api.applications.list(params),
    placeholderData: keepPreviousData,
    refetchInterval: options.poll ? 3_000 : false,
    refetchIntervalInBackground: true,
  });
}

export function useApplication(id: string) {
  return useQuery({
    queryKey: queryKeys.application(id),
    queryFn: () => api.applications.get(id),
    // Poll while documents are being generated or files exported.
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const busy = IN_PROGRESS.includes(data.status) || data.processing.some((p) => p.status === "queued" || p.status === "running");
      return busy ? 1_500 : false;
    },
    refetchIntervalInBackground: true,
  });
}

export function useApplicationMutation<V>(id: string, fn: (vars: V) => Promise<ApplicationDetail>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (detail) => {
      client.setQueryData(queryKeys.application(id), detail);
      void client.invalidateQueries({ queryKey: queryKeys.applications, exact: false, refetchType: "inactive" });
      void client.invalidateQueries({ queryKey: queryKeys.dashboard });
    },
  });
}

// ---------------------------------------------------------------- operations

export function useAnalytics(days: number) {
  return useQuery({ queryKey: queryKeys.analytics(days), queryFn: () => api.analytics(days) });
}

export function useBackups() {
  return useQuery({ queryKey: queryKeys.backups, queryFn: api.backups.list });
}

export function usePdfConverter() {
  return useQuery({ queryKey: queryKeys.pdfConverter, queryFn: api.system.pdfConverter, staleTime: 60_000 });
}
