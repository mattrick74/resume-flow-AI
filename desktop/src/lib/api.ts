import { getBackendConnection } from "@/lib/backend";
import type {
  Analytics,
  AnswerBank,
  AnswerBankEntry,
  AppSettings,
  ApplicationDetail,
  ApplicationSummary,
  BackupInfo,
  BatchGaps,
  BatchResult,
  CandidateDetail,
  CandidateListItem,
  CandidatePreferences,
  CandidateProfile,
  ConfirmResult,
  CoverageView,
  CoverLetterContent,
  DashboardSummary,
  DocumentTemplate,
  DocView,
  EvidenceOverview,
  ExperienceRecord,
  FollowUp,
  Health,
  ImportResult,
  JobDetail,
  JobRequirements,
  JobSummary,
  PdfConverterStatus,
  ProcessingJobInfo,
  ProcessingStageEventsResponse,
  QueueResult,
  ReextractResult,
  ResumeContent,
  ResumeVersionInfo,
  SettingsResponse,
  SkillAnswer,
  SkillGapReport,
  SuggestAdditions,
  TemplateSamples,
  TemplatesResponse,
  TemplateStyle,
  VerificationStatus,
} from "@/lib/types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { baseUrl, token } = await getBackendConnection();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // FormData sets its own multipart boundary; only JSON strings get a content type here.
  if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the ResumeFlow engine.");
  }

  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(
      response.status,
      error?.code ?? `http_${response.status}`,
      error?.message ?? response.statusText,
      error?.details,
    );
  }
  return body as T;
}

const json = (value: unknown) => JSON.stringify(value);
const post = <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "POST", body: body === undefined ? undefined : json(body) });
const patch = <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PATCH", body: json(body) });
const put = <T>(path: string, body: unknown) => apiFetch<T>(path, { method: "PUT", body: json(body) });
const del = <T>(path: string) => apiFetch<T>(path, { method: "DELETE" });

function query(params: Record<string, string | number | undefined | null>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function upload<T>(path: string, file: File) {
  const form = new FormData();
  form.append("file", file, file.name);
  return apiFetch<T>(path, { method: "POST", body: form });
}

export interface CandidateUpdate {
  full_name?: string;
  email?: string | null;
  phone?: string | null;
  location?: string | null;
  status?: "active" | "archived";
  profile?: CandidateProfile;
  preferences?: CandidatePreferences;
  consent_confirmed?: boolean;
  /** v2.0: notes for answering application questions ("" clears them). */
  answer_notes?: string;
}

export interface JobCreate {
  job_url?: string;
  raw_jd: string;
  company?: string;
  title?: string;
  recruiter?: string;
  rate?: string;
  location?: string;
  employment_type?: string;
  notes?: string;
  confirm_duplicate?: boolean;
}

export const api = {
  health: () => apiFetch<Health>("/health"),
  dashboard: {
    summary: () => apiFetch<DashboardSummary>("/dashboard/summary"),
  },
  settings: {
    get: () => apiFetch<SettingsResponse>("/settings"),
    update: (changes: Partial<AppSettings>) => patch<SettingsResponse>("/settings", changes),
    storeSecret: (name: string, value: string) => put<void>(`/settings/secrets/${encodeURIComponent(name)}`, { value }),
    deleteSecret: (name: string) => del<void>(`/settings/secrets/${encodeURIComponent(name)}`),
  },
  candidates: {
    list: (params: { status?: string; q?: string } = {}) => apiFetch<CandidateListItem[]>(`/candidates${query(params)}`),
    get: (id: string) => apiFetch<CandidateDetail>(`/candidates/${id}`),
    import: (file: File) => upload<ImportResult>("/candidates/import", file),
    addVersion: (id: string, file: File) => upload<ImportResult>(`/candidates/${id}/resumes`, file),
    update: (id: string, changes: CandidateUpdate) => patch<CandidateDetail>(`/candidates/${id}`, changes),
    remove: (id: string) => del<void>(`/candidates/${id}`),
    version: (id: string, versionId: string) => apiFetch<ResumeVersionInfo>(`/candidates/${id}/resumes/${versionId}`),
    applyVersion: (id: string, versionId: string) => post<CandidateDetail>(`/candidates/${id}/resumes/${versionId}/apply-profile`),
    reextract: (id: string, versionId: string) => post<ReextractResult>(`/candidates/${id}/resumes/${versionId}/reextract`),
    completeReview: (id: string) => post<CandidateDetail>(`/candidates/${id}/review-complete`),
    addSkill: (id: string, body: { skill_name: string; evidence?: string | null; verification_status?: VerificationStatus }) =>
      post<CandidateDetail>(`/candidates/${id}/skills`, body),
    updateSkill: (id: string, skillId: string, body: { skill_name?: string; evidence?: string | null; verification_status?: VerificationStatus }) =>
      patch<CandidateDetail>(`/candidates/${id}/skills/${skillId}`, body),
    deleteSkill: (id: string, skillId: string) => del<CandidateDetail>(`/candidates/${id}/skills/${skillId}`),
    /** Skills separated by commas, semicolons or new lines. */
    addSkills: (id: string, text: string) => post<{ added: string[]; already: string[]; candidate: CandidateDetail }>(`/candidates/${id}/skills/bulk`, { text }),
    verifySkillsWithEvidence: (id: string) => post<{ verified: number; candidate: CandidateDetail }>(`/candidates/${id}/skills/verify-with-evidence`),
    /** v1.8: Have used / Have not used / Unsure for job skills; descriptions of work become drafted bullets. */
    confirmSkills: (id: string, answers: SkillAnswer[], source_job_ids: string[] = []) =>
      post<ConfirmResult>(`/candidates/${id}/skills/confirm-bulk`, { answers, source_job_ids }),
    evidence: (id: string) => apiFetch<EvidenceOverview>(`/candidates/${id}/experience-evidence`),
    addEvidence: (id: string, body: { role_id: string; skills: string[]; context: string; project?: string | null; outcome?: string | null; origin?: "interview" | "manual" }) =>
      post<EvidenceOverview>(`/candidates/${id}/experience-evidence`, body),
    updateEvidence: (
      id: string,
      recordId: string,
      body: { narrative?: string; context?: string; project?: string; outcome?: string; skills?: string[]; role_id?: string; status?: ExperienceRecord["status"]; redraft?: boolean; acknowledge?: boolean },
    ) => patch<FollowUp & { records: ExperienceRecord[]; overview: EvidenceOverview }>(`/candidates/${id}/experience-evidence/${recordId}`, body),
    deleteEvidence: (id: string, recordId: string) => del<FollowUp & { overview: EvidenceOverview }>(`/candidates/${id}/experience-evidence/${recordId}`),
    approveEvidence: (id: string, ids: string[], options: { acknowledge?: boolean; edits?: Record<string, string> } = {}) =>
      post<FollowUp & { records: ExperienceRecord[]; overview: EvidenceOverview }>(`/candidates/${id}/experience-evidence/approve`, { ids, ...options }),
  },
  jobs: {
    list: (params: { status?: string; q?: string; limit?: number } = {}) => apiFetch<JobSummary[]>(`/jobs${query(params)}`),
    get: (id: string) => apiFetch<JobDetail>(`/jobs/${id}`),
    create: (body: JobCreate) => post<JobDetail>("/jobs", body),
    saveMany: (items: JobCreate[], options: { process?: boolean; confirm_duplicates?: boolean } = {}) =>
      post<BatchResult>("/jobs/batch", { items, ...options }),
    process: (body: { job_ids?: string[]; all_saved?: boolean }) => post<QueueResult>("/jobs/process", body),
    update: (id: string, body: Partial<JobCreate>) => patch<JobDetail>(`/jobs/${id}`, body),
    remove: (id: string) => del<void>(`/jobs/${id}`),
    analyze: (id: string) => post<JobDetail>(`/jobs/${id}/analyze`),
    match: (id: string) => post<JobDetail>(`/jobs/${id}/match`),
    updateRequirements: (id: string, requirements: JobRequirements) => put<JobDetail>(`/jobs/${id}/requirements`, requirements),
    /** v1.8: every job skill classified for a candidate (default: the top-ranked one). */
    skillGaps: (id: string, candidateId?: string) => apiFetch<SkillGapReport>(`/jobs/${id}/skill-gaps${query({ candidate_id: candidateId })}`),
    /** Missing skills across jobs (default: every job waiting for answers), most frequent required skills first. */
    batchGaps: (job_ids: string[] = []) => post<BatchGaps>("/jobs/skill-gaps/batch", { job_ids }),
    rematch: (id: string) => post<JobDetail>(`/jobs/${id}/rematch`),
  },
  applications: {
    list: (params: { status?: string; candidate_id?: string; date_from?: string; date_to?: string; q?: string; limit?: number } = {}) =>
      apiFetch<ApplicationSummary[]>(`/applications${query(params)}`),
    get: (id: string) => apiFetch<ApplicationDetail>(`/applications/${id}`),
    create: (body: { job_id: string; candidate_id: string; override_reason?: string | null; instructions?: string | null }) =>
      post<ApplicationDetail>("/applications", body),
    generate: (id: string, body: { document: "both" | "resume" | "cover_letter"; section: "all" | "summary" | "skills" | "experience"; instructions?: string | null }) =>
      post<ApplicationDetail>(`/applications/${id}/generate`, body),
    editDocument: (id: string, type: "resume" | "cover_letter", content: ResumeContent | CoverLetterContent) =>
      put<ApplicationDetail>(`/applications/${id}/documents/${type}`, { content }),
    documentVersion: (id: string, type: "resume" | "cover_letter", version: number) =>
      apiFetch<DocView<ResumeContent | CoverLetterContent>>(`/applications/${id}/documents/${type}/versions/${version}`),
    approve: (id: string, acknowledge_warnings = false) => post<ApplicationDetail>(`/applications/${id}/approve`, { acknowledge_warnings }),
    export: (id: string) => post<ApplicationDetail>(`/applications/${id}/export`),
    retryPdf: (id: string) => post<ApplicationDetail>(`/applications/${id}/retry-pdf`),
    outcome: (id: string, status: "SUBMITTED" | "INTERVIEW" | "REJECTED" | "OFFER") => post<ApplicationDetail>(`/applications/${id}/outcome`, { status }),
    /** v1.8 GEN-11: missing job skills and drafted bullets awaiting approval. */
    suggestAdditions: (id: string) => post<SuggestAdditions>(`/applications/${id}/suggest-additions`),
    /** Approves drafted bullets and (by default) regenerates both documents with them. */
    approveAdditions: (id: string, body: { record_ids: string[]; edits?: Record<string, string>; acknowledge?: boolean; regenerate?: boolean }) =>
      post<FollowUp & { records: ExperienceRecord[]; regenerating: boolean; application: ApplicationDetail }>(`/applications/${id}/approve-additions`, body),
    coverage: (id: string) => apiFetch<CoverageView>(`/applications/${id}/coverage`),
  },
  processing: {
    list: (params: { status?: string; application_id?: string; limit?: number } = {}) => apiFetch<ProcessingJobInfo[]>(`/processing-jobs${query(params)}`),
    events: (afterId?: number, limit = 200) =>
      apiFetch<ProcessingStageEventsResponse>(`/processing-jobs/events${query({ after_id: afterId, limit })}`),
    retry: (id: string) => post<ProcessingJobInfo>(`/processing-jobs/${id}/retry`),
    cancel: (id: string) => post<ProcessingJobInfo>(`/processing-jobs/${id}/cancel`),
  },
  templates: {
    list: () => apiFetch<TemplatesResponse>("/templates"),
    import: (file: File, name?: string) => {
      const form = new FormData();
      form.append("file", file, file.name);
      if (name) form.append("name", name);
      return apiFetch<DocumentTemplate>("/templates/import", { method: "POST", body: form });
    },
    fromResume: (body: { candidate_id: string; version_id: string; name?: string }) => post<DocumentTemplate>("/templates/from-resume", body),
    update: (id: string, body: { name?: string; style?: TemplateStyle }) => patch<DocumentTemplate>(`/templates/${id}`, body),
    remove: (id: string) => del<void>(`/templates/${id}`),
    samples: (key: string) => post<TemplateSamples>("/templates/sample", { key }),
  },
  analytics: (days = 30) => apiFetch<Analytics>(`/analytics${query({ days })}`),
  backups: {
    list: () => apiFetch<BackupInfo[]>("/backups"),
    create: () => post<BackupInfo>("/backups"),
    restore: (name: string) => post<{ restored: string; safety_backup: string }>("/backups/restore", { name }),
  },
  /** v2.0: saved answers to common application questions (global, or per candidate). */
  answerBank: {
    get: (candidateId?: string | null) => apiFetch<AnswerBank>(`/answer-bank${candidateId ? `?candidate_id=${encodeURIComponent(candidateId)}` : ""}`),
    save: (entry: { candidate_id?: string | null; key?: string | null; question?: string | null; answer: string }) => put<AnswerBankEntry>("/answer-bank", entry),
    remove: (id: string) => del<void>(`/answer-bank/${encodeURIComponent(id)}`),
  },
  system: {
    open: (path: string, reveal = false) => post<void>("/system/open", { path, reveal }),
    openUrl: (url: string) => post<void>("/system/open-url", { url }),
    pdfConverter: () => apiFetch<PdfConverterStatus>("/system/pdf-converter"),
  },
};
