import { vi } from "vitest";

import { DEFAULT_TEMPLATE_STYLE } from "@/lib/templateStyle";
import type { AppSettings, DashboardSummary, DocumentTemplate, SettingsResponse, TemplatesResponse } from "@/lib/types";

export const DEFAULT_SETTINGS: AppSettings = {
  output_root: "C:\\Users\\Operator\\Documents\\ResumeFlow AI\\Applications",
  daily_goal: 100,
  worker_concurrency: 2,
  ai_provider: "openai",
  analysis_model: "gpt-6-luna",
  writing_model: "gpt-6.1-sol",
  model_prices: {},
  daily_budget_usd: null,
  monthly_budget_usd: null,
  ai_extract_on_import: true,
  semantic_matching: false,
  match_weights: { mandatory_skills: 35, experience: 25, seniority: 15, preferred_skills: 10, industry: 10, other: 5 },
  resume_template: "professional",
  pdf_converter: "auto",
  export_mode: "auto",
  output_formats: "pdf",
  skip_known_urls: true,
  require_verification: false,
  gap_confirmation: "off",
  auto_approve_bullets: true,
  hard_requirement_gates: false,
  bullet_density: "standard",
  writing_instructions: "",
  libreoffice_path: null,
  auto_fetch_job_urls: false,
  backup_retention_count: 14,
  theme: "system",
};

export const EMPTY_SUMMARY: DashboardSummary = {
  date: "2026-10-14",
  daily_goal: 100,
  processing_failed: 0,
  candidates_total: 0,
  candidates_ready: 0,
  saved_jobs: 0,
  jobs_needing_confirmation: 0,
  jobs_failed: 0,
  budget: {
    today_usd: 0,
    month_usd: 0,
    daily_budget_usd: null,
    monthly_budget_usd: null,
    daily_exceeded: false,
    monthly_exceeded: false,
    daily_warning: false,
    monthly_warning: false,
  },
  jobs_today: 0,
  generated_today: 0,
  exported_today: 0,
  failed_today: 0,
  awaiting_review: 0,
  queue_depth: 0,
  cost_today_usd: 0,
  avg_generation_ms: null,
  recent_activity: [],
};

export const BUILTIN_TEMPLATES: DocumentTemplate[] = [
  { key: "professional", id: null, name: "Professional", builtin: true, style: DEFAULT_TEMPLATE_STYLE, source_filename: null, notes: [], created_at: null },
  {
    key: "compact",
    id: null,
    name: "Compact",
    builtin: true,
    style: { ...DEFAULT_TEMPLATE_STYLE, font: "Arial", heading_font: "Arial", size: 9.5, name_size: 16, name_color: "222222", heading_size: 10, heading_color: "222222", rule_color: "222222", muted_color: "606060", margin: 0.55, paragraph_space: 1 },
    source_filename: null,
    notes: [],
    created_at: null,
  },
];

export const templatesResponse = (selected = "professional", extra: DocumentTemplate[] = []): TemplatesResponse => ({
  selected,
  templates: [...BUILTIN_TEMPLATES, ...extra],
});

export interface RecordedCall {
  method: string;
  path: string;
  body: unknown;
  headers: Headers;
}

type Handler = (call: RecordedCall) => Response | undefined;

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

export { json };

/**
 * In-memory stand-in for the sidecar. `override` can intercept any call
 * (return undefined to fall through to the default behaviour).
 */
export function mockBackend(options: { summary?: DashboardSummary; settings?: Partial<AppSettings>; override?: Handler } = {}) {
  const state = {
    settings: { ...DEFAULT_SETTINGS, ...options.settings },
    secrets: { openai_api_key: false } as Record<string, boolean>,
  };
  const calls: RecordedCall[] = [];

  const settingsResponse = (): SettingsResponse => ({
    settings: state.settings,
    secrets_configured: { ...state.secrets },
    paths: { data_dir: "C:\\Data\\ResumeFlowAI", database: "C:\\Data\\ResumeFlowAI\\resumeflow.db" },
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const call: RecordedCall = {
      method: init.method ?? "GET",
      path: url.replace(/^\/api/, "").replace(/^https?:\/\/[^/]+/, ""),
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      headers: new Headers(init.headers),
    };
    calls.push(call);

    const overridden = options.override?.(call);
    if (overridden) return overridden;

    const { method, path } = call;
    if (path === "/health") return json({ status: "ok", version: "0.1.0", schema_revision: "0001", schema_head: "0001" });
    if (path === "/dashboard/summary") return json(options.summary ?? EMPTY_SUMMARY);
    if (path === "/settings" && method === "GET") return json(settingsResponse());
    if (path === "/settings" && method === "PATCH") {
      state.settings = { ...state.settings, ...(call.body as Partial<AppSettings>) };
      return json(settingsResponse());
    }
    const secret = /^\/settings\/secrets\/(\w+)$/.exec(path);
    if (secret) {
      state.secrets[secret[1]] = method === "PUT";
      return new Response(null, { status: 204 });
    }
    const listPath = path.split("?")[0];
    if (method === "GET" && ["/candidates", "/jobs", "/applications", "/backups", "/processing-jobs"].includes(listPath)) return json([]);
    if (method === "GET" && listPath === "/templates") return json(templatesResponse(state.settings.resume_template));
    if (method === "GET" && listPath === "/system/pdf-converter") {
      return json({ configured: "auto", libreoffice_path: null, word_available: true, active: "word" });
    }
    if (method === "GET" && listPath === "/analytics") {
      return json({
        days: [],
        totals: { applications: 0, generated: 0, exported: 0, cost_usd: 0, cost_per_export_usd: null, daily_goal: 100 },
        by_model: [],
        by_operation: [],
        queue: { jobs: 0, retried: 0, retry_rate: 0, failed: 0, cancelled: 0, pending: 0 },
        budget: EMPTY_SUMMARY.budget,
      });
    }
    return json({ error: { code: "http_404", message: "Not Found" } }, 404);
  });

  vi.stubGlobal("fetch", fetchMock);
  return { calls, state, fetchMock };
}
