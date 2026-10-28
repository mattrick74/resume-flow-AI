// Mirrors the sidecar's response models (backend/resumeflow/api/*).

export type PipelineStatus =
  | "DRAFT"
  | "ANALYZING"
  | "MATCHED"
  | "GENERATING"
  | "VALIDATING"
  | "AWAITING_REVIEW"
  | "APPROVED"
  | "EXPORTED"
  | "NO_ELIGIBLE_CANDIDATE"
  | "FAILED"
  | "SUBMITTED"
  | "REJECTED"
  | "INTERVIEW"
  | "OFFER";

export type ThemePreference = "system" | "light" | "dark";
export type VerificationStatus = "unverified" | "verified" | "rejected";
export type EligibilityStatus = "eligible" | "ineligible" | "needs_review";

export interface Health {
  status: "ok" | "degraded";
  version: string;
  schema_revision: string | null;
  schema_head: string | null;
}

export interface MatchWeights {
  mandatory_skills: number;
  experience: number;
  seniority: number;
  preferred_skills: number;
  industry: number;
  other: number;
}

export interface AppSettings {
  output_root: string;
  daily_goal: number;
  worker_concurrency: number;
  ai_provider: "openai";
  analysis_model: string;
  writing_model: string;
  model_prices: Record<string, { input: number; output: number }>;
  daily_budget_usd: number | null;
  monthly_budget_usd: number | null;
  ai_extract_on_import: boolean;
  /** v2.0: job analysis and ranking never call the AI. */
  local_analysis_only: boolean;
  semantic_matching: boolean;
  match_weights: MatchWeights;
  /** A built-in template ("professional", "compact") or "custom:<id>" for one copied from a resume. */
  resume_template: string;
  pdf_converter: "auto" | "libreoffice" | "word" | "none";
  /** Legacy compatibility field. The current pipeline always exports as soon as both documents are written. */
  export_mode: "review" | "auto" | "always";
  /** pdf: save PDFs only (the Word file is kept only if no PDF can be made). pdf_docx: save both. */
  output_formats: "pdf" | "pdf_docx";
  /** Legacy setting retained for older backends; current versions always process duplicate jobs. */
  skip_known_urls: boolean;
  /** Off (default): every fact in a profile is used as entered, except rejected skills. On: only ticked facts after review. */
  require_verification: boolean;
  /** off (default): generate straight away from the profile as it is. ask: background processing waits until the top candidate's missing job skills are answered. */
  gap_confirmation: "ask" | "off";
  /** On (default): a bullet written from your own description of the work is added at once, without an approval step. */
  auto_approve_bullets: boolean;
  /** Off (default): work authorization, clearance, licenses and location don't block or rank candidates. On: pass/fail gates. */
  hard_requirement_gates: boolean;
  /** Bullets per role: concise 2–4 / 1–3, standard 3–6 / 2–4, detailed 5–8 / 3–5 (recent / older roles). */
  bullet_density: "concise" | "standard" | "detailed";
  /** Notes the AI writer follows for every application. */
  writing_instructions: string;
  libreoffice_path: string | null;
  auto_fetch_job_urls: boolean;
  backup_retention_count: number;
  theme: ThemePreference;
}

/** Visual style of resumes and cover letters. Colors are 6-digit hex without "#"; sizes in points; margin in inches. */
export interface TemplateStyle {
  font: string;
  heading_font: string;
  size: number;
  name_size: number;
  name_bold: boolean;
  name_align: "left" | "center";
  name_color: string;
  heading_size: number;
  heading_bold: boolean;
  heading_caps: boolean;
  heading_rule: boolean;
  heading_color: string;
  rule_color: string;
  text_color: string;
  muted_color: string;
  margin: number;
  paragraph_space: number;
}

export interface DocumentTemplate {
  key: string;
  id: string | null;
  name: string;
  builtin: boolean;
  style: TemplateStyle;
  source_filename: string | null;
  notes: string[];
  created_at: string | null;
}

export interface TemplatesResponse {
  selected: string;
  templates: DocumentTemplate[];
}

export interface TemplateSamples {
  resume_docx: string;
  resume_pdf: string | null;
  cover_letter_docx: string;
  cover_letter_pdf: string | null;
}

export interface SettingsResponse {
  settings: AppSettings;
  secrets_configured: Record<string, boolean>;
  paths: { data_dir: string; database: string };
}

export interface ActivityItem {
  application_id: string;
  candidate_name: string;
  company: string | null;
  title: string | null;
  status: PipelineStatus;
  updated_at: string;
}

export interface BudgetStatus {
  today_usd: number;
  month_usd: number;
  daily_budget_usd: number | null;
  monthly_budget_usd: number | null;
  daily_exceeded: boolean;
  monthly_exceeded: boolean;
  daily_warning: boolean;
  monthly_warning: boolean;
}

export interface DashboardSummary {
  date: string;
  daily_goal: number;
  processing_failed: number;
  candidates_total: number;
  candidates_ready: number;
  /** Saved jobs not yet queued for generation. */
  saved_jobs: number;
  /** Jobs whose background processing waits for answers about missing job skills. */
  jobs_needing_confirmation: number;
  /** Jobs whose automatic processing failed; retry them from the Jobs page. */
  jobs_failed: number;
  budget: BudgetStatus;
  jobs_today: number;
  generated_today: number;
  exported_today: number;
  failed_today: number;
  awaiting_review: number;
  queue_depth: number;
  cost_today_usd: number;
  avg_generation_ms: number | null;
  recent_activity: ActivityItem[];
}

export interface ValidationIssue {
  loc: (string | number)[];
  msg: string;
  type: string;
}

// ---------------------------------------------------------------- candidates

export interface Bullet {
  id: string;
  text: string;
  verified: boolean;
}

export interface Role {
  id: string;
  employer: string;
  title: string;
  location: string | null;
  start: string | null;
  end: string | null;
  bullets: Bullet[];
  verified: boolean;
}

export interface Education {
  id: string;
  institution: string;
  degree: string | null;
  field: string | null;
  end_year: number | null;
  verified: boolean;
}

export interface Certification {
  id: string;
  name: string;
  issuer: string | null;
  year: number | null;
  verified: boolean;
}

export interface CandidateProfile {
  headline: string | null;
  summary: string | null;
  summary_verified: boolean;
  employment: Role[];
  education: Education[];
  certifications: Certification[];
  links: string[];
  work_authorization: string | null;
  work_authorization_verified: boolean;
  source_version_id: string | null;
}

export type WorkArrangement = "remote" | "hybrid" | "onsite";

export interface CandidatePreferences {
  work_arrangements: WorkArrangement[];
  preferred_locations: string[];
  willing_to_relocate: boolean | null;
  employment_types: string[];
  availability: string | null;
  notes: string | null;
}

export interface CandidateSkill {
  id: string;
  skill_name: string;
  normalized_name: string;
  evidence: string | null;
  verification_status: VerificationStatus;
  source_resume_version_id: string | null;
  updated_at: string;
  /** The operator's answer when a job asked for this skill (Have used / Have not used / Unsure). */
  decision?: SkillDecision | null;
  decided_at?: string | null;
  /** Roles where it was confirmed; empty = a general skill (Skills section only). */
  role_ids?: string[];
  years?: number | null;
  proficiency?: string | null;
  last_used?: string | null;
}

export type SkillDecision = "used" | "not_used" | "unsure";

export interface ResumeVersionInfo {
  id: string;
  version: number;
  original_filename: string;
  original_file_path: string;
  mime_type: string;
  file_sha256: string;
  verification_status: "pending_review" | "partially_verified" | "verified";
  extraction_source: "heuristic" | "ai" | null;
  extraction_warnings: string[];
  created_at: string;
  extracted_text?: string;
}

export interface ReextractResult {
  candidate: CandidateDetail;
  extraction_source: "ai" | "heuristic";
  warnings: string[];
  roles: number;
  bullets: number;
  education: number;
  skills_added: number;
  /** Skills from the earlier reading of this resume that this reading doesn't support. */
  skills_removed: number;
  /** Verified facts that came back word for word and kept their tick. */
  kept_verified: number;
}

export interface Readiness {
  verified_roles: number;
  verified_skills: number;
  ready_for_matching: boolean;
  blockers: string[];
  /** Not blocking, but each leaves something out of every resume (unverified education, roles without bullets…). */
  warnings: string[];
}

export interface CandidateListItem {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  location: string | null;
  status: "active" | "archived";
  latest_version: number | null;
  skills: Record<VerificationStatus, number>;
  reviewed: boolean;
  consent_confirmed: boolean;
  applications: number;
  readiness: Readiness;
  updated_at: string;
}

export interface CandidateDetail {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  location: string | null;
  status: "active" | "archived";
  profile: CandidateProfile;
  preferences: CandidatePreferences;
  skills: CandidateSkill[];
  resume_versions: ResumeVersionInfo[];
  profile_reviewed_at: string | null;
  consent_confirmed_at: string | null;
  /** v2.0: notes the AI reads when answering application questions (never used in resumes). */
  answer_notes?: string;
  newer_extraction_available: boolean;
  applications: number;
  readiness: Readiness;
  years_experience: number;
  /** Increments whenever confirmed experience is approved, changed or removed. */
  profile_revision?: number;
  created_at: string;
  updated_at: string;
}

export interface ImportResult {
  candidate: CandidateDetail;
  created: boolean;
  version: number;
  skills_added: number;
  extraction_source: "heuristic" | "ai";
  warnings: string[];
}

// ---------------------------------------------------------------- jobs & matching

export interface JobRequirements {
  title: string | null;
  employer: string | null;
  seniority: "intern" | "junior" | "mid" | "senior" | "lead" | "principal" | "executive" | "unknown";
  min_years_experience: number | null;
  mandatory_skills: string[];
  preferred_skills: string[];
  duties: string[];
  industry: string | null;
  location: string | null;
  work_arrangement: "onsite" | "hybrid" | "remote" | "unknown";
  work_authorization: string | null;
  security_clearance: string | null;
  required_credentials: string[];
  employment_type: string | null;
  other_hard_requirements: string[];
}

export interface Gate {
  name: string;
  status: "pass" | "fail" | "unknown";
  detail: string;
}

export interface SkillCheck {
  skill: string;
  matched: boolean;
  method: "verified_skill" | "experience" | "semantic" | null;
  evidence: string | null;
  explanation?: string;
}

export interface SkillComponent {
  score: number;
  weight: number;
  matched: SkillCheck[];
  missing: string[];
}

export interface MatchDetails {
  excluded?: boolean;
  reasons?: string[];
  gates?: Gate[];
  components?: {
    mandatory_skills: SkillComponent;
    preferred_skills: SkillComponent;
    experience: { score: number; weight: number; title_similarity: number; duty_coverage: number | null };
    seniority: { score: number; weight: number; years: number; required_years: number | null; level_score: number | null };
    industry: { score: number; weight: number; industry: string | null };
    other: { score: number; weight: number };
  };
  manual_checks?: string[];
  explanation?: string;
  years_experience?: number;
  /** Skills matched or lost since the previous ranking. */
  skill_changes?: { added: string[]; removed: string[] } | null;
}

export interface CandidateMatch {
  candidate_id: string;
  candidate_name: string;
  /** Verified match score: confirmed evidence only. Used to rank and choose. */
  score: number | null;
  /** If every unanswered job skill were confirmed. Shown only. */
  potential_score?: number | null;
  /** Before the latest re-ranking. */
  previous_score?: number | null;
  rank: number | null;
  eligibility_status: EligibilityStatus;
  details: MatchDetails;
}

export interface JobProcessing {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  error: string | null;
}

export interface JobSummary {
  id: string;
  job_url: string;
  company: string | null;
  title: string | null;
  location: string | null;
  status: PipelineStatus;
  created_at: string;
  analyzed_at: string | null;
  top_candidate?: string | null;
  eligible_count?: number;
  applications?: number;
  processing?: JobProcessing | null;
  application?: { id: string; status: PipelineStatus; candidate_id: string } | null;
  /** Background processing waits for answers about missing job skills. */
  needs_confirmation?: boolean;
}

export interface BatchItemResult {
  index: number;
  /** duplicate/skipped remain accepted for compatibility with older backends. */
  status: "created" | "duplicate" | "skipped" | "invalid";
  job?: JobSummary;
  code?: string;
  message?: string;
  details?: { matches?: DuplicateMatch[] } | unknown;
  /** Legacy response field used when an older backend resumes a previously saved job. */
  queued_job_id?: string;
}

export interface QueueResult {
  queued: string[];
  skipped: { job_id: string; reason: string }[];
}

export interface BatchResult extends QueueResult {
  results: BatchItemResult[];
}

export interface JobDetail extends Omit<JobSummary, "applications"> {
  recruiter: string | null;
  rate: string | null;
  employment_type: string | null;
  notes: string | null;
  raw_jd: string;
  normalized_url: string;
  requirements: JobRequirements | null;
  requirements_source: "ai" | "heuristic" | "manual" | "cache" | null;
  warnings: { code: string; message: string }[];
  matches: CandidateMatch[];
  applications: { id: string; candidate_id: string; status: PipelineStatus; created_at: string }[];
  processing?: JobProcessing | null;
}

export interface DuplicateMatch {
  job_id: string;
  title: string | null;
  company: string | null;
  created_at: string;
  reason: "same_url" | "same_text" | "similar_text";
  similarity: number;
}

// ---------------------------------------------------------------- applications & documents

export interface Issue {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  location: string;
}

export interface ResumeBullet {
  text: string;
  source_ids: string[];
}

export interface ResumeRole {
  role_id: string;
  employer: string;
  title: string;
  location: string | null;
  start: string | null;
  end: string | null;
  bullets: ResumeBullet[];
  /** The employer's "Technologies:" line: technologies confirmed for this role or named in its bullets. */
  technologies?: string[];
}

export interface Contact {
  name: string;
  email: string | null;
  phone: string | null;
  location: string | null;
  links: string[];
}

export interface ResumeContent {
  contact: Contact;
  headline: string | null;
  summary: string;
  skills: string[];
  experience: ResumeRole[];
  education: Partial<Education>[];
  certifications: Partial<Certification>[];
}

export interface CoverLetterContent {
  date: string;
  company: string | null;
  job_title: string | null;
  greeting: string;
  paragraphs: { text: string; evidence_ids: string[] }[];
  closing: string;
  signature: string;
  contact: Contact | null;
}

export interface DocView<T> {
  id: string;
  version: number;
  status: "draft" | "approved" | "superseded";
  origin: string | null;
  mode: "ai" | "deterministic" | "edited" | null;
  content: T;
  issues: Issue[];
  issue_counts: { error: number; warning: number; info: number };
  approved_at: string | null;
  created_at: string;
}

export interface ProcessingJobInfo {
  id: string;
  job_type: "generate_documents" | "export_application" | "convert_pdf" | string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  error: string | null;
  retry_count: number;
  created_at: string;
  finished_at: string | null;
  application_id?: string | null;
}

export type ProcessingStage =
  | "jd_analysis"
  | "candidate_matching"
  | "resume_writing"
  | "cover_letter_writing"
  | "resume_render"
  | "cover_letter_render"
  | "pdf_conversion"
  | "document_export"
  | "queue_idle"
  | (string & {});

export interface ProcessingStageEvent {
  id: number;
  processing_job_id: string | null;
  job_id: string | null;
  application_id: string | null;
  stage: ProcessingStage;
  status: "succeeded" | "failed" | "skipped";
  message: string;
  duration_ms: number;
  details: Record<string, unknown>;
  created_at: string;
}

export interface ProcessingStageEventsResponse {
  events: ProcessingStageEvent[];
  next_after_id: number;
  queue_depth: number;
}

export interface ApplicationSummary {
  id: string;
  job_id: string;
  candidate_id: string;
  candidate_name: string;
  company: string | null;
  title: string | null;
  job_url: string;
  application_date: string;
  sequence_number: number;
  status: PipelineStatus;
  selection_method: "automatic" | "manual_override";
  output_directory: string | null;
  pdf_status: "succeeded" | "failed" | "skipped" | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  exported_at: string | null;
}

export interface OutputFile {
  id: string;
  type: "resume" | "cover_letter" | "job_description" | "job_url" | "metadata";
  format: "docx" | "pdf" | "txt" | "json";
  version: number;
  path: string | null;
  exists: boolean;
}

export interface ApplicationDetail extends ApplicationSummary {
  /** "operator" for legacy/manual API calls or "automatic" for the current pipeline. */
  approved_by: "operator" | "automatic" | null;
  /** This application's notes for the AI writer. */
  instructions: string | null;
  override_reason: string | null;
  approved_at: string | null;
  submitted_at: string | null;
  requirements: JobRequirements | null;
  match: { score: number | null; rank: number | null; eligibility_status: EligibilityStatus; details: MatchDetails } | null;
  resume: DocView<ResumeContent> | null;
  cover_letter: DocView<CoverLetterContent> | null;
  versions: Record<"resume" | "cover_letter", { id: string; version: number; status: string; origin: string | null; created_at: string }[]>;
  files: OutputFile[];
  processing: ProcessingJobInfo[];
  cost: { runs: number; input_tokens: number; output_tokens: number; estimated_usd: number };
  /** The candidate's profile revision in this application's snapshot, and the current one. */
  profile_revision?: number | null;
  current_profile_revision?: number;
  addition_ids?: string[];
  coverage?: CoverageSummary | null;
}

// ---------------------------------------------------------------- confirmed experience (v1.8)

export type GapStatus = "matched" | "confirmed" | "plausible" | "unknown" | "rejected";

export interface SkillGapItem {
  skill: string;
  normalized: string;
  kind: "required" | "preferred";
  status: GapStatus;
  match: "exact" | "equivalent" | "partial" | "missing";
  evidence: string | null;
  answer: SkillDecision | null;
  role_ids: string[];
  needs_answer: boolean;
  pending_suggestion: boolean;
}

export interface RoleOption {
  id: string;
  label: string;
}

export interface SkillGapReport {
  job_id: string;
  job_title: string | null;
  company: string | null;
  candidate_id: string;
  candidate_name: string;
  items: SkillGapItem[];
  hard_gates: Gate[];
  counts: Record<GapStatus, number>;
  unanswered: number;
  pending_suggestions: number;
  score: number | null;
  potential_score: number | null;
  previous_score: number | null;
  roles: RoleOption[];
}

export interface BatchGapSkill {
  skill: string;
  normalized: string;
  required_count: number;
  preferred_count: number;
  impact: number;
  jobs: { job_id: string; title: string | null; company: string | null; kind: "required" | "preferred" }[];
  answer: SkillDecision | null;
  status: GapStatus;
  evidence: string | null;
  needs_answer: boolean;
}

export interface BatchGaps {
  jobs: number;
  candidates: { candidate_id: string; candidate_name: string; roles: RoleOption[]; job_ids: string[]; skills: BatchGapSkill[] }[];
  skipped: { job_id: string; reason: string }[];
}

export interface SkillAnswer {
  skill: string;
  decision: SkillDecision;
  role_ids?: string[];
  context?: string | null;
  project?: string | null;
  outcome?: string | null;
  years?: number | null;
  proficiency?: "beginner" | "intermediate" | "advanced" | "expert" | null;
  last_used?: string | null;
  note?: string | null;
}

export interface RecordIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
}

export interface ExperienceRecord {
  id: string;
  role_id: string;
  role: string;
  role_found: boolean;
  skills: string[];
  context: string;
  project: string | null;
  outcome: string | null;
  narrative: string | null;
  status: "suggested" | "approved" | "rejected";
  origin: "gap" | "interview" | "manual";
  drafted_by: "ai" | "offline" | "operator" | null;
  issues: RecordIssue[];
  issue_counts: { error: number; warning: number };
  source_job_ids: string[];
  created_at: string;
  updated_at: string;
  approved_at: string | null;
}

export interface EvidenceOverview {
  profile_revision: number;
  records: ExperienceRecord[];
  history: {
    skill: string;
    normalized: string;
    decision: SkillDecision;
    roles: string[];
    years: number | null;
    proficiency: string | null;
    last_used: string | null;
    note: string | null;
    decided_at: string;
    jobs: { id: string; title: string | null; company: string | null }[];
  }[];
  roles: RoleOption[];
}

export interface FollowUp {
  /** Waiting jobs that continue now that nothing is left to answer. */
  resumed: string[];
  /** Other jobs re-ranked with the new answers. */
  rematched: string[];
}

export interface ConfirmResult extends FollowUp {
  answers: { skill: string; normalized: string; decision: SkillDecision; issues: RecordIssue[] }[];
  /** Bullets added at once (Settings: no approval step). */
  approved?: ExperienceRecord[];
  /** Bullets waiting for approval. */
  suggestions: ExperienceRecord[];
  candidate: CandidateDetail;
}

export interface CoverageGroup {
  total: number;
  in_resume: number;
  confirmed: number;
  in_original_resume: number | null;
  missing: string[];
}

export interface CoverageSummary {
  required: CoverageGroup;
  preferred: CoverageGroup;
  keyword_coverage_pct: number | null;
  confirmed_coverage_pct: number | null;
  original_coverage_pct: number | null;
  unsupported_claims: number;
  duplicate_pairs?: number;
}

export interface CoverageReport extends CoverageSummary {
  rows: { skill: string; normalized: string; kind: "required" | "preferred"; in_resume: boolean; confirmed: boolean; in_original_resume: boolean | null }[];
  duplicates: [string, string][];
  suggestions: string[];
  note: string;
}

export interface ResumeChanges {
  skills: { added: string[]; kept: string[]; not_used: string[] };
  bullets: { role: string; text: string; kind: "added" | "kept" | "rewritten"; original: string | null; addition_ids: string[] }[];
  not_used_bullets: { role: string; text: string }[];
  previous: { bullets_added: string[]; bullets_removed: string[]; skills_added: string[]; skills_removed: string[] } | null;
}

export interface CoverageView {
  coverage: CoverageReport;
  changes: ResumeChanges;
  profile_revision: number | null;
  current_profile_revision: number;
}

export interface SuggestAdditions {
  gaps: SkillGapReport;
  suggestions: ExperienceRecord[];
  profile_revision: number | null;
  current_profile_revision: number;
}

// ---------------------------------------------------------------- operations

export interface AnalyticsDay {
  date: string;
  applications: number;
  generated: number;
  exported: number;
  cost_usd: number;
  ai_runs: number;
  ai_failures: number;
}

export interface Analytics {
  days: AnalyticsDay[];
  totals: { applications: number; generated: number; exported: number; cost_usd: number; cost_per_export_usd: number | null; daily_goal: number };
  by_model: { model: string; runs: number; input_tokens: number; output_tokens: number; cost_usd: number; failures: number }[];
  by_operation: { operation: string; runs: number; failures: number; failure_rate: number; avg_duration_ms: number | null; p95_duration_ms: number | null; cost_usd: number }[];
  queue: { jobs: number; retried: number; retry_rate: number; failed: number; cancelled: number; pending: number };
  budget: BudgetStatus;
  experience?: {
    gaps_identified: number;
    skills_confirmed: number;
    skills_rejected: number;
    skills_unsure: number;
    bullets_suggested: number;
    bullets_approved: number;
    jobs_paused: number;
    jobs_resumed: number;
    matches_rescored: number;
    avg_minutes_to_answer: number | null;
    coverage_before_pct: number | null;
    coverage_after_pct: number | null;
  };
}

export interface BackupInfo {
  name: string;
  size_bytes: number;
  created_at: string;
}

export interface PdfConverterStatus {
  configured: AppSettings["pdf_converter"];
  libreoffice_path: string | null;
  word_available: boolean;
  active: "libreoffice" | "word" | null;
}

/** v2.0 answer bank: saved answers the extension fills into application forms. */
export interface AnswerBankEntry {
  id: string;
  candidate_id: string | null;
  key: string;
  question: string | null;
  answer: string;
  updated_at: string;
}

export interface AnswerBank {
  standard: { key: string; label: string; eeo?: boolean }[];
  entries: AnswerBankEntry[];
}
