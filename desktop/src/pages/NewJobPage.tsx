import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Files, FileText, LoaderCircle, Save, ScanSearch } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { BatchJobForm } from "@/components/jobs/BatchJobForm";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api, type JobCreate } from "@/lib/api";
import { queryKeys, useDashboardSummary, useVerificationRequired } from "@/lib/queries";
import type { DuplicateMatch } from "@/lib/types";
import { cn, formatRelativeTime } from "@/lib/utils";

const EMPTY: JobCreate = { job_url: "", raw_jd: "", company: "", title: "", recruiter: "", rate: "", location: "", employment_type: "", notes: "" };
const REASON = { same_url: "Same posting URL", same_text: "Identical description", similar_text: "Very similar description" };

function validOptionalUrl(value?: string) {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) return true;
  try {
    const url = new URL(trimmed);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export function NewJobPage() {
  const navigate = useNavigate();
  const summary = useDashboardSummary();
  const verificationRequired = useVerificationRequired();
  const [params, setParams] = useSearchParams();
  const mode = params.get("mode") === "batch" ? "batch" : "single";
  const [form, setForm] = useState<JobCreate>(EMPTY);
  const [showMore, setShowMore] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState<null | "analyze" | "save">(null);
  // skipped: the posting URL is already in history, so there is nothing to "create anyway".
  const [duplicates, setDuplicates] = useState<{ matches: DuplicateMatch[]; action: "analyze" | "save"; skipped?: boolean } | null>(null);
  const client = useQueryClient();

  const urlError = submitted && !validOptionalUrl(form.job_url) ? "Enter the full posting URL starting with https://, or leave it blank" : null;
  const jdError = submitted && form.raw_jd.trim().length < 50 ? "Paste the full job description" : null;
  const set = (key: keyof JobCreate, value: string) => setForm((f) => ({ ...f, [key]: value }));

  const submit = async (action: "analyze" | "save" = "analyze", confirmDuplicate = false) => {
    setSubmitted(true);
    if (!validOptionalUrl(form.job_url) || form.raw_jd.trim().length < 50) return;
    setBusy(action);
    const clean = Object.fromEntries(Object.entries(form).map(([k, v]) => [k, typeof v === "string" ? v.trim() || undefined : v])) as unknown as JobCreate;
    try {
      if (action === "save") {
        const [result] = (await api.jobs.saveMany([clean], { confirm_duplicates: confirmDuplicate })).results;
        if (result.status === "duplicate" || result.status === "skipped") {
          setDuplicates({ matches: (result.details as { matches: DuplicateMatch[] }).matches, action, skipped: result.status === "skipped" });
          return;
        }
        if (result.status === "invalid") throw new ApiError(422, result.code ?? "invalid", result.message ?? "Invalid job");
        void client.invalidateQueries({ queryKey: queryKeys.jobs });
        void client.invalidateQueries({ queryKey: queryKeys.dashboard });
        toast.success("Saved for later", { description: "Generate it from the Jobs page whenever you're ready." });
        setForm(EMPTY);
        setSubmitted(false);
        return;
      }
      const job = await api.jobs.create({ ...clean, confirm_duplicate: confirmDuplicate });
      toast.success(job.status === "MATCHED" ? "Requirements extracted and candidates ranked" : "Job analyzed — no eligible candidate yet");
      navigate(`/jobs/${job.id}`);
    } catch (error) {
      if (error instanceof ApiError && (error.code === "duplicate_job" || error.code === "url_in_history")) {
        setDuplicates({ matches: (error.details as { matches: DuplicateMatch[] }).matches, action, skipped: error.code === "url_in_history" });
      } else {
        toast.error(action === "save" ? "Couldn't save this job" : "Couldn't analyze this job", { description: describeError(error) });
      }
    } finally {
      setBusy(null);
    }
  };

  const ready = summary.data?.candidates_ready ?? null;
  return (
    <>
      <PageHeader title="New Job" description="Paste each job description. A posting URL is optional and is never visited unless you enable automatic fetching." />
      {ready === 0 && (
        <Alert variant="warning" title="No candidates are ready for matching" className="mb-4" action={<Button asChild variant="outline" size="sm"><Link to="/candidates">Review candidates</Link></Button>}>
          Import resumes and confirm each candidate's authorization first{verificationRequired ? ", then verify their details and complete each review" : ""}.
        </Alert>
      )}
      <Tabs value={mode} onValueChange={(value) => setParams(value === "batch" ? { mode: "batch" } : {}, { replace: true })}>
        <TabsList aria-label="How many jobs">
          <TabsTrigger value="single">
            <FileText /> One job
          </TabsTrigger>
          <TabsTrigger value="batch">
            <Files /> Several jobs
          </TabsTrigger>
        </TabsList>
        {/* Both stay mounted so switching tabs never loses what was typed; `hidden` keeps the other one out of reach. */}
        <TabsContent value="batch" forceMount hidden={mode !== "batch"}>
          <BatchJobForm />
        </TabsContent>
        <TabsContent value="single" forceMount hidden={mode !== "single"}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            void submit();
          }
        }}
        noValidate
      >
        <Card className="space-y-4 p-5">
          <div className="space-y-1.5">
            <Label htmlFor="job-url">Job posting URL <span className="font-normal text-muted-foreground">(optional)</span></Label>
            <Input id="job-url" value={form.job_url ?? ""} onChange={(e) => set("job_url", e.target.value)} placeholder="https://careers.example.com/jobs/123" aria-invalid={urlError ? true : undefined} autoFocus />
            <p className={cn("text-xs text-muted-foreground", urlError && "text-destructive")}>{urlError ?? "Leave blank when the posting has no URL."}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="job-jd">Job description</Label>
            <Textarea
              id="job-jd"
              rows={16}
              value={form.raw_jd}
              onChange={(e) => set("raw_jd", e.target.value)}
              placeholder="Paste the complete job description, including requirements and responsibilities."
              aria-invalid={jdError ? true : undefined}
              className="font-[inherit] leading-relaxed"
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span className={cn(jdError && "text-destructive")}>{jdError ?? "Treated as data only — instructions inside it are never followed."}</span>
              <span className="tabular-nums">{form.raw_jd.trim() ? form.raw_jd.trim().split(/\s+/).length : 0} words</span>
            </div>
          </div>

          <button type="button" onClick={() => setShowMore((v) => !v)} className="flex items-center gap-1 text-sm font-medium text-muted-foreground hover:text-foreground" aria-expanded={showMore}>
            <ChevronDown className={cn("size-4 transition-transform", showMore && "rotate-180")} /> Optional details
          </button>
          {showMore && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {(
                [
                  ["company", "Company"],
                  ["title", "Job title"],
                  ["location", "Location"],
                  ["recruiter", "Recruiter"],
                  ["rate", "Rate / salary"],
                  ["employment_type", "Employment type"],
                ] as const
              ).map(([key, label]) => (
                <div key={key} className="space-y-1.5">
                  <Label htmlFor={`job-${key}`}>{label}</Label>
                  <Input id={`job-${key}`} value={form[key] ?? ""} onChange={(e) => set(key, e.target.value)} />
                </div>
              ))}
              <div className="space-y-1.5 sm:col-span-2 lg:col-span-3">
                <Label htmlFor="job-notes">Notes</Label>
                <Textarea id="job-notes" rows={2} value={form.notes ?? ""} onChange={(e) => set("notes", e.target.value)} />
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 border-t pt-4">
            <Button type="submit" disabled={busy !== null}>
              {busy === "analyze" ? <LoaderCircle className="animate-spin" /> : <ScanSearch />}
              {busy === "analyze" ? "Analyzing and ranking…" : "Analyze & match"}
              {!busy && <Kbd className="border-primary-foreground/20 bg-primary-foreground/10 text-primary-foreground">Ctrl ↵</Kbd>}
            </Button>
            <Button type="button" variant="outline" onClick={() => void submit("save")} disabled={busy !== null}>
              {busy === "save" ? <LoaderCircle className="animate-spin" /> : <Save />} Save for later
            </Button>
            <Button type="button" variant="ghost" onClick={() => { setForm(EMPTY); setSubmitted(false); }} disabled={busy !== null}>
              Clear
            </Button>
            {busy === "analyze" && <span className="text-sm text-muted-foreground">Extracting requirements, checking hard gates and scoring every ready candidate.</span>}
          </div>
        </Card>
      </form>
        </TabsContent>
      </Tabs>

      <Modal
        open={duplicates !== null}
        onOpenChange={(open) => !open && setDuplicates(null)}
        title={duplicates?.skipped ? "This job posting is already in your history" : "This job may already be in ResumeFlow"}
        description={
          duplicates?.skipped
            ? "It was skipped, so nothing new was saved or generated. Open the earlier job to see its documents."
            : "Creating it again can lead to duplicate applications."
        }
        footer={
          duplicates?.skipped ? (
            <Button variant="outline" onClick={() => setDuplicates(null)}>
              Close
            </Button>
          ) : (
            <>
              <Button variant="ghost" onClick={() => setDuplicates(null)}>
                Cancel
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  const action = duplicates?.action;
                  setDuplicates(null);
                  void submit(action, true);
                }}
              >
                {duplicates?.action === "save" ? "Save anyway" : "Create anyway"}
              </Button>
            </>
          )
        }
      >
        <ul className="divide-y rounded-lg border">
          {duplicates?.matches.map((d) => (
            <li key={d.job_id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{[d.title, d.company].filter(Boolean).join(" · ") || "Untitled job"}</div>
                <div className="text-xs text-muted-foreground">
                  {REASON[d.reason]}
                  {d.reason === "similar_text" && ` (${Math.round(d.similarity * 100)}% overlap)`} · added {formatRelativeTime(d.created_at)}
                </div>
              </div>
              <Button asChild variant="link" size="sm">
                <Link to={`/jobs/${d.job_id}`}>Open</Link>
              </Button>
            </li>
          ))}
        </ul>
      </Modal>
    </>
  );
}
