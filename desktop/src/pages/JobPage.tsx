import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ExternalLink, FileText, Pencil, RefreshCw, Save, ScanSearch, Sparkles, TriangleAlert, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";

import { ErrorState, describeError } from "@/components/common/ErrorState";
import { InstructionsField } from "@/components/common/InstructionsField";
import { JobGapsPanel } from "@/components/experience/JobGapsPanel";
import { StatusBadge } from "@/components/common/StatusBadge";
import { MatchCard } from "@/components/jobs/MatchCard";
import { RequirementsEditor } from "@/components/jobs/RequirementsEditor";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { queryKeys, useJob, useJobMutation, useProcessJobs, useSettings } from "@/lib/queries";
import type { CandidateMatch, JobDetail, JobRequirements } from "@/lib/types";
import { cn } from "@/lib/utils";

const SOURCE_LABEL = { ai: "AI extraction", heuristic: "Offline rules", manual: "Edited by you", cache: "Reused from identical JD" } as const;

export function JobPage() {
  const { id = "" } = useParams();
  const job = useJob(id);
  if (job.isPending) return <Skeleton className="h-[600px]" />;
  if (job.isError) return <ErrorState error={job.error} onRetry={() => void job.refetch()} />;
  return <JobView job={job.data} />;
}

function JobView({ job }: { job: JobDetail }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [requirements, setRequirements] = useState<JobRequirements | null>(job.requirements);
  const savedKey = JSON.stringify(job.requirements);
  useEffect(() => setRequirements(JSON.parse(savedKey) as JobRequirements | null), [savedKey]);
  const dirty = JSON.stringify(requirements) !== savedKey;
  const [showJd, setShowJd] = useState(false);
  const [override, setOverride] = useState<CandidateMatch | null>(null);
  const [reason, setReason] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [instructions, setInstructions] = useState("");
  const autoExport = (useSettings().data?.settings.export_mode ?? "auto") !== "review";

  const saveRequirements = useJobMutation(job.id, (req: JobRequirements) => api.jobs.updateRequirements(job.id, req));
  const reanalyze = useJobMutation(job.id, () => api.jobs.analyze(job.id));
  const rematch = useJobMutation(job.id, () => api.jobs.match(job.id));

  const scored = job.matches.filter((m) => !m.details.excluded);
  const eligible = scored.filter((m) => m.rank !== null).sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
  const review = scored.filter((m) => m.eligibility_status === "needs_review").sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const ineligible = scored.filter((m) => m.eligibility_status === "ineligible").sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const excluded = job.matches.filter((m) => m.details.excluded);
  const existing = job.applications.filter((a) => a.status !== "FAILED" && a.status !== "REJECTED");

  const choose = async (match: CandidateMatch, overrideReason?: string) => {
    setCreating(true);
    try {
      const app = await api.applications.create({ job_id: job.id, candidate_id: match.candidate_id, override_reason: overrideReason ?? null, instructions: instructions.trim() || null });
      toast.success(`Generating documents for ${match.candidate_name}`, { description: autoExport ? "The resume and cover letter files are saved as soon as they're ready." : "They'll wait in the Review Queue for your approval." });
      navigate(`/applications/${app.id}`);
    } catch (error) {
      toast.error("Couldn't create the application", { description: describeError(error) });
    } finally {
      setCreating(false);
      setOverride(null);
      setReason("");
    }
  };

  const processJobs = useProcessJobs();
  const inQueue = job.processing?.status === "queued" || job.processing?.status === "running" || job.status === "ANALYZING";
  const generateNow = () =>
    processJobs.mutate(
      { job_ids: [job.id] },
      {
        onSuccess: (result) => {
          if (result.queued.length) toast.success("Queued — the tailored documents will appear in the Review Queue");
          else toast.info("Not queued", { description: result.skipped[0]?.reason });
          void client.invalidateQueries({ queryKey: queryKeys.job(job.id) });
        },
        onError: (e) => toast.error("Couldn't queue this job", { description: describeError(e) }),
      },
    );

  const busy = saveRequirements.isPending || reanalyze.isPending || rematch.isPending || inQueue;
  return (
    <div className="pb-16">
      <Link to="/jobs" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Jobs
      </Link>
      <div className="mb-5 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{job.title ?? "Untitled job"}</h1>
            <StatusBadge status={job.status} subject="job" />
          </div>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
            {[job.company, job.location, job.employment_type, job.rate].filter(Boolean).join(" · ")}
            <button
              type="button"
              className="inline-flex max-w-md items-center gap-1 truncate text-primary hover:underline"
              onClick={() => api.system.openUrl(job.job_url).catch((e) => toast.error("Couldn't open link", { description: describeError(e) }))}
              title="Open the posting in your browser"
            >
              {job.normalized_url} <ExternalLink className="size-3.5 shrink-0" />
            </button>
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
          <Pencil /> Edit details
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => rematch.mutate(undefined, { onSuccess: () => toast.success("Candidates re-ranked"), onError: (e) => toast.error("Matching failed", { description: describeError(e) }) })}>
          <RefreshCw className={cn(rematch.isPending && "animate-spin")} /> Re-run matching
        </Button>
      </div>
      <JobDetailsModal job={job} open={editing} onOpenChange={setEditing} />

      {inQueue ? (
        <Alert className="mb-3" title={job.processing?.status === "queued" ? "Queued for tailored documents" : "Working on this job in the background"}>
          Requirements are extracted, candidates ranked, and the top eligible candidate gets a tailored resume and cover letter for your review.
          {job.processing?.status === "running" && <Progress value={job.processing.progress} className="mt-2 max-w-xs" aria-label="Processing progress" />}
        </Alert>
      ) : (
        (job.status === "DRAFT" || job.status === "FAILED") &&
        existing.length === 0 && (
          <Alert
            variant={job.status === "FAILED" ? "destructive" : "info"}
            className="mb-3"
            title={job.status === "FAILED" ? "Processing didn't finish" : "Saved for later"}
            action={
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={busy || processJobs.isPending} onClick={() => rematch.mutate(undefined, { onSuccess: () => toast.success("Requirements extracted and candidates ranked"), onError: (e) => toast.error("Analysis failed", { description: describeError(e) }) })}>
                  <ScanSearch /> Analyze only
                </Button>
                <Button size="sm" disabled={busy || processJobs.isPending} onClick={generateNow}>
                  <Sparkles /> {job.status === "FAILED" ? "Retry" : "Generate now"}
                </Button>
              </div>
            }
          >
            {job.status === "FAILED"
              ? "Retry to run the whole pipeline again, or analyze only to check the requirements first."
              : "Generate now runs the whole pipeline in the background. Analyze only extracts requirements and ranks candidates so you can choose yourself."}
          </Alert>
        )
      )}

      {job.warnings.filter((w) => w.code !== "processing_failed" || job.status === "FAILED").map((warning) => (
        <Alert key={warning.code + warning.message} variant="warning" className="mb-3">
          {warning.message}
        </Alert>
      ))}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,420px)_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="flex-row items-center justify-between pb-0">
              <CardTitle>Requirements</CardTitle>
              {job.requirements_source && <Badge variant="outline">{SOURCE_LABEL[job.requirements_source]}</Badge>}
            </CardHeader>
            <CardContent>
              {requirements ? (
                <RequirementsEditor value={requirements} onChange={setRequirements} />
              ) : (
                <p className="text-sm text-muted-foreground">Requirements haven't been extracted yet.</p>
              )}
              <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
                <Button
                  size="sm"
                  disabled={!dirty || busy || !requirements}
                  onClick={() =>
                    requirements &&
                    saveRequirements.mutate(requirements, {
                      onSuccess: () => toast.success("Requirements saved and candidates re-ranked"),
                      onError: (e) => toast.error("Couldn't save requirements", { description: describeError(e) }),
                    })
                  }
                >
                  <Save /> Save & re-match
                </Button>
                {dirty && (
                  <Button size="sm" variant="ghost" onClick={() => setRequirements(job.requirements)}>
                    Discard
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  disabled={busy}
                  onClick={() => reanalyze.mutate(undefined, { onSuccess: () => toast.success("Requirements re-extracted"), onError: (e) => toast.error("Analysis failed", { description: describeError(e) }) })}
                >
                  <ScanSearch /> Re-extract
                </Button>
              </div>
            </CardContent>
          </Card>
          <Card>
            <button type="button" className="flex w-full items-center gap-2 px-5 py-4 text-left text-sm font-semibold" onClick={() => setShowJd((v) => !v)} aria-expanded={showJd}>
              <FileText className="size-4 text-muted-foreground" /> Original job description
              <ChevronDown className={cn("ml-auto size-4 transition-transform", showJd && "rotate-180")} />
            </button>
            {showJd && <pre className="max-h-[480px] overflow-auto border-t px-5 py-4 font-sans text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground">{job.raw_jd}</pre>}
          </Card>
        </div>

        <div className="space-y-5">
          {job.status === "NO_ELIGIBLE_CANDIDATE" && (
            <Alert variant="warning" title="No candidate meets this job's hard requirements">
              ResumeFlow won't present an ineligible candidate as a fit. Check the requirements on the left, review candidates that need a check, or choose one manually with a recorded reason.
            </Alert>
          )}
          {eligible[0] && job.requirements && <JobGapsPanel key={eligible[0].candidate_id} jobId={job.id} candidateId={eligible[0].candidate_id} />}
          {scored.length > 0 && (
            <Card className="p-4">
              <InstructionsField id="job-ai-instructions" value={instructions} onChange={setInstructions} />
            </Card>
          )}
          {eligible.length > 0 && (
            <section>
              <h2 className="mb-2 text-sm font-semibold">Recommended</h2>
              <div className="space-y-3">
                {eligible.slice(0, 3).map((match) => (
                  <MatchCard
                    key={match.candidate_id}
                    match={match}
                    highlight={match.rank === 1}
                    disabled={creating || inQueue}
                    actionLabel={match.rank === 1 ? `Generate for ${match.candidate_name.split(" ")[0]}` : "Choose instead…"}
                    onSelect={() => (match.rank === 1 ? void choose(match) : setOverride(match))}
                  />
                ))}
              </div>
            </section>
          )}
          <MatchGroup title="Other eligible candidates" matches={eligible.slice(3)} onChoose={setOverride} disabled={creating || inQueue} />
          <MatchGroup title="Need a check before they can be ranked" matches={review} onChoose={setOverride} disabled={creating || inQueue} defaultOpen={eligible.length === 0} />
          <MatchGroup title="Fail a hard requirement" matches={ineligible} onChoose={setOverride} disabled={creating || inQueue} />
          {excluded.length > 0 && (
            <Card className="p-4">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                <Users className="size-4 text-muted-foreground" /> Not considered ({excluded.length})
              </div>
              <ul className="space-y-1.5 text-sm">
                {excluded.map((m) => (
                  <li key={m.candidate_id} className="flex flex-wrap gap-x-2">
                    <Link to={`/candidates/${m.candidate_id}`} className="font-medium hover:underline">
                      {m.candidate_name}
                    </Link>
                    <span className="text-muted-foreground">— {m.details.reasons?.join("; ")}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {job.matches.length === 0 && job.status !== "DRAFT" && !inQueue && (
            <Alert title="No candidates yet" action={<Button asChild size="sm" variant="outline"><Link to="/candidates">Import candidates</Link></Button>}>
              Import candidates and get them ready, then re-run matching.
            </Alert>
          )}
        </div>
      </div>

      <Modal
        open={override !== null}
        onOpenChange={(open) => !open && setOverride(null)}
        title={`Choose ${override?.candidate_name} instead?`}
        description="Overrides are recorded with the application and its metadata. Documents still use only facts from this candidate's profile."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOverride(null)}>
              Cancel
            </Button>
            <Button disabled={!reason.trim() || creating} onClick={() => override && void choose(override, reason.trim())}>
              Choose and generate
            </Button>
          </>
        }
      >
        {override?.eligibility_status === "ineligible" && (
          <Alert variant="destructive" className="mb-3" title="This candidate fails a hard requirement">
            {override.details.gates?.filter((g) => g.status === "fail").map((g) => g.detail).join(" ")}
          </Alert>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="override-reason">Reason (required)</Label>
          <Textarea id="override-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Client requested this candidate; authorization confirmed by phone" />
        </div>
      </Modal>
    </div>
  );
}

const DETAIL_FIELDS = [
  ["title", "Job title"],
  ["company", "Company"],
  ["location", "Location"],
  ["employment_type", "Employment type"],
  ["rate", "Rate / salary"],
  ["recruiter", "Recruiter"],
] as const;

function JobDetailsModal({ job, open, onOpenChange }: { job: JobDetail; open: boolean; onOpenChange: (open: boolean) => void }) {
  const initial = () => Object.fromEntries(DETAIL_FIELDS.map(([key]) => [key, job[key] ?? ""])) as Record<(typeof DETAIL_FIELDS)[number][0], string>;
  const [form, setForm] = useState(initial);
  const [notes, setNotes] = useState(job.notes ?? "");
  useEffect(() => {
    if (open) {
      setForm(initial());
      setNotes(job.notes ?? "");
    }
  }, [open]);
  const save = useJobMutation(job.id, () => api.jobs.update(job.id, { ...Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v.trim()])), notes: notes.trim() }));
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Edit job details"
      description="The company and title name the exported files and appear in the cover letter."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={save.isPending}
            onClick={() =>
              save.mutate(undefined, {
                onSuccess: () => {
                  toast.success("Job details saved");
                  onOpenChange(false);
                },
                onError: (e) => toast.error("Not saved", { description: describeError(e) }),
              })
            }
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {DETAIL_FIELDS.map(([key, label]) => (
          <div key={key} className="space-y-1.5">
            <Label htmlFor={`jd-${key}`}>{label}</Label>
            <Input id={`jd-${key}`} value={form[key]} onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
          </div>
        ))}
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="jd-notes">Notes</Label>
          <Textarea id="jd-notes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
      </div>
    </Modal>
  );
}

function MatchGroup({
  title,
  matches,
  onChoose,
  disabled,
  defaultOpen = false,
}: {
  title: string;
  matches: CandidateMatch[];
  onChoose: (match: CandidateMatch) => void;
  disabled: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!matches.length) return null;
  return (
    <section>
      <button type="button" onClick={() => setOpen((v) => !v)} className="mb-2 flex items-center gap-1.5 text-sm font-semibold" aria-expanded={open}>
        {title === "Fail a hard requirement" && <TriangleAlert className="size-4 text-destructive" aria-hidden />}
        {title} <span className="font-normal text-muted-foreground">({matches.length})</span>
        <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-3">
          {matches.map((match) => (
            <MatchCard key={match.candidate_id} match={match} actionLabel="Choose instead…" onSelect={() => onChoose(match)} disabled={disabled} />
          ))}
        </div>
      )}
    </section>
  );
}
