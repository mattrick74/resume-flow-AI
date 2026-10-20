import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  BadgeCheck,
  Ban,
  Check,
  ChevronDown,
  FileDown,
  FileText,
  FolderOpen,
  Gauge,
  LoaderCircle,
  Pencil,
  RefreshCw,
  RotateCcw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { ErrorState, describeError } from "@/components/common/ErrorState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { CoveragePanel } from "@/components/experience/CoveragePanel";
import { useRegenerateWithAdditions } from "@/components/experience/ImprovePanel";
import { GateList } from "@/components/jobs/MatchCard";
import { ScoreBreakdown } from "@/components/jobs/ScoreBreakdown";
import { CoverLetterEditor, CoverLetterPreview, wordCount } from "@/components/review/CoverLetterDocument";
import { ResumeEditor, ResumePreview } from "@/components/review/ResumeDocument";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { InstructionsField } from "@/components/common/InstructionsField";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Modal } from "@/components/ui/modal";
import { Progress } from "@/components/ui/progress";
import { ResizableSplit } from "@/components/ui/resizable-split";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useApplication, useApplicationMutation } from "@/lib/queries";
import type { ApplicationDetail, CoverLetterContent, DocView, OutputFile, PipelineStatus, ResumeContent } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

type DocType = "resume" | "cover_letter";
type Regenerate = { document: "both" | "resume" | "cover_letter"; section: "all" | "summary" | "skills" | "experience"; label: string };
type Outcome = "SUBMITTED" | "INTERVIEW" | "REJECTED" | "OFFER";

const REGENERATE_OPTIONS: Regenerate[] = [
  { document: "both", section: "all", label: "Both documents" },
  { document: "resume", section: "all", label: "Resume — everything" },
  { document: "resume", section: "summary", label: "Resume — summary only" },
  { document: "resume", section: "skills", label: "Resume — skills only" },
  { document: "resume", section: "experience", label: "Resume — experience only" },
  { document: "cover_letter", section: "all", label: "Cover letter" },
];

// Generated documents are exported as soon as they're written: no review or approval steps.
const STEPS: { label: string; states: PipelineStatus[] }[] = [
  { label: "Generate", states: ["MATCHED", "GENERATING", "VALIDATING", "AWAITING_REVIEW", "APPROVED", "FAILED"] },
  { label: "Exported", states: ["EXPORTED"] },
  { label: "Submitted", states: ["SUBMITTED", "INTERVIEW", "OFFER", "REJECTED"] },
];

export function ApplicationPage() {
  const { id = "" } = useParams();
  const app = useApplication(id);
  if (app.isPending) return <Skeleton className="h-[640px]" />;
  if (app.isError) return <ErrorState error={app.error} onRetry={() => void app.refetch()} />;
  return <ApplicationView app={app.data} />;
}

function ApplicationView({ app }: { app: ApplicationDetail }) {
  const id = app.id;
  const onError = (title: string) => (error: unknown) => toast.error(title, { description: describeError(error) });
  const [regenerate, setRegenerate] = useState<Regenerate | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const approve = useApplicationMutation(id, (ack: boolean) => api.applications.approve(id, ack));
  const exportApp = useApplicationMutation(id, () => api.applications.export(id));
  const retryPdf = useApplicationMutation(id, () => api.applications.retryPdf(id));
  const [instructions, setInstructions] = useState(app.instructions ?? "");
  const generate = useApplicationMutation(id, (r: Regenerate & { instructions: string }) =>
    api.applications.generate(id, { document: r.document, section: r.section, instructions: r.instructions }),
  );
  const record = useApplicationMutation(id, (o: Outcome) => api.applications.outcome(id, o));
  const regenerateWithAdditions = useRegenerateWithAdditions(app);
  const retryJob = useApplicationMutation(id, (jobId: string) => api.processing.retry(jobId).then(() => api.applications.get(id)));
  const cancelJob = useApplicationMutation(id, (jobId: string) => api.processing.cancel(jobId).then(() => api.applications.get(id)));

  const active = app.processing.find((p) => p.status === "queued" || p.status === "running");
  const failed = app.processing.find((p) => p.status === "failed");
  const generating = app.status === "GENERATING" || app.status === "VALIDATING";
  const canRegenerate = ["AWAITING_REVIEW", "APPROVED", "EXPORTED", "FAILED"].includes(app.status);
  const stepIndex = STEPS.findIndex((s) => s.states.includes(app.status));

  const doApprove = () => approve.mutate(true, { onSuccess: () => toast.success("Documents approved"), onError: onError("Couldn't approve") });

  return (
    <div className="pb-10">
      <Link to="/review" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Review queue
      </Link>
      <div className="mb-4 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{app.candidate_name}</h1>
            <StatusBadge status={app.status} />
            {app.selection_method === "manual_override" && <Badge variant="outline">Manual selection</Badge>}
            {app.approved_by === "automatic" && ["APPROVED", "EXPORTED"].includes(app.status) && (
              <Badge variant="secondary" title="Settings → After generating: export automatically">Exported automatically</Badge>
            )}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            <Link to={`/jobs/${app.job_id}`} className="hover:underline">
              {[app.title, app.company].filter(Boolean).join(" at ") || "Job"}
            </Link>
            {" · "}
            {String(app.sequence_number).padStart(3, "0")} on {formatDate(app.application_date)}
            {app.cost.runs > 0 && ` · AI cost $${app.cost.estimated_usd.toFixed(3)}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canRegenerate && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" disabled={generate.isPending}>
                  <RefreshCw /> Regenerate <ChevronDown className="size-3.5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Creates a new version</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {REGENERATE_OPTIONS.map((option) => (
                  <DropdownMenuItem
                    key={option.label}
                    onSelect={() => {
                      setInstructions(app.instructions ?? "");
                      setRegenerate(option);
                    }}
                  >
                    <Sparkles /> {option.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {app.status === "AWAITING_REVIEW" && (
            <Button size="sm" disabled={approve.isPending} onClick={doApprove}>
              <BadgeCheck /> Approve
            </Button>
          )}
          {app.status === "APPROVED" && (
            <Button size="sm" disabled={exportApp.isPending || Boolean(active)} onClick={() => exportApp.mutate(undefined, { onSuccess: () => toast.success("Export started"), onError: onError("Export failed") })}>
              <FileDown /> Export files
            </Button>
          )}
          {app.status === "EXPORTED" && (
            <Button size="sm" onClick={() => setOutcome("SUBMITTED")}>
              <Send /> Mark as submitted
            </Button>
          )}
          {(app.status === "SUBMITTED" || app.status === "INTERVIEW") && (
            <>
              {app.status === "SUBMITTED" && (
                <Button size="sm" variant="outline" onClick={() => setOutcome("INTERVIEW")}>
                  Interview
                </Button>
              )}
              <Button size="sm" variant="outline" onClick={() => setOutcome("OFFER")}>
                Offer
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setOutcome("REJECTED")}>
                Rejected
              </Button>
            </>
          )}
        </div>
      </div>

      <ol className="mb-4 flex items-center gap-1 text-xs" aria-label="Progress">
        {STEPS.map((step, index) => (
          <li key={step.label} className="flex items-center gap-1">
            <span
              className={cn(
                "flex items-center gap-1 rounded-full px-2.5 py-1 font-medium",
                index < stepIndex && "bg-success/10 text-success",
                index === stepIndex && "bg-primary text-primary-foreground",
                index > stepIndex && "bg-muted text-muted-foreground",
              )}
              aria-current={index === stepIndex ? "step" : undefined}
            >
              {index < stepIndex && <Check className="size-3" aria-hidden />}
              {step.label}
            </span>
            {index < STEPS.length - 1 && <span className="h-px w-4 bg-border" aria-hidden />}
          </li>
        ))}
      </ol>

      {active && (
        <Card className="mb-4 flex items-center gap-4 px-4 py-3">
          <LoaderCircle className="size-4 animate-spin text-primary" aria-hidden />
          <div className="flex-1">
            <div className="text-sm font-medium">
              {active.job_type === "generate_documents" ? "Writing tailored documents…" : active.job_type === "export_application" ? "Writing files…" : "Converting to PDF…"}
            </div>
            <Progress value={active.progress} className="mt-2 h-1.5" aria-label="Progress" />
            {active.retry_count > 0 && <div className="mt-1 text-xs text-muted-foreground">Retry {active.retry_count}: {active.error}</div>}
          </div>
          <Button variant="ghost" size="sm" onClick={() => cancelJob.mutate(active.id, { onError: onError("Couldn't cancel") })}>
            <Ban /> Cancel
          </Button>
        </Card>
      )}
      {app.status === "FAILED" && (
        <Alert
          variant="destructive"
          className="mb-4"
          title="Generation failed"
          action={
            failed ? (
              <Button size="sm" variant="outline" onClick={() => retryJob.mutate(failed.id, { onSuccess: () => toast.success("Retrying"), onError: onError("Couldn't retry") })}>
                <RotateCcw /> Retry
              </Button>
            ) : undefined
          }
        >
          {app.error}
        </Alert>
      )}
      {app.status === "AWAITING_REVIEW" && app.error && (
        <Alert variant="warning" className="mb-4" title="Waiting for you">
          {app.error}
        </Alert>
      )}
      {canRegenerate && (app.current_profile_revision ?? 0) > (app.profile_revision ?? 0) && (
        <Alert
          className="mb-4"
          title="Newer confirmed experience"
          action={
            <Button size="sm" variant="outline" disabled={generate.isPending} onClick={() => void regenerateWithAdditions()}>
              <RefreshCw /> Regenerate with approved additions
            </Button>
          }
        >
          {app.candidate_name}'s profile changed since these documents were written (confirmed skills or approved bullets).
        </Alert>
      )}
      {app.status === "EXPORTED" && app.pdf_status === "failed" && (
        <Alert
          variant="warning"
          className="mb-4"
          title="PDF conversion failed — the DOCX files were saved"
          action={
            <Button size="sm" variant="outline" disabled={retryPdf.isPending || Boolean(active)} onClick={() => retryPdf.mutate(undefined, { onError: onError("Couldn't retry") })}>
              <RotateCcw /> Retry PDF
            </Button>
          }
        >
          {app.error}
        </Alert>
      )}

      {generating && !app.resume ? (
        <Card className="flex h-80 flex-col items-center justify-center gap-3 text-center">
          <LoaderCircle className="size-6 animate-spin text-primary" aria-hidden />
          <div className="text-sm text-muted-foreground">Documents appear here as soon as they're written.</div>
        </Card>
      ) : (
        <Tabs defaultValue="documents">
          <TabsList>
            <TabsTrigger value="documents">
              <FileText /> Documents
            </TabsTrigger>
            <TabsTrigger value="files">
              <FolderOpen /> Files
            </TabsTrigger>
            <TabsTrigger value="match">Match</TabsTrigger>
            <TabsTrigger value="coverage">
              <Gauge /> Coverage
            </TabsTrigger>
          </TabsList>
          <TabsContent value="documents">
            <ResizableSplit
              storageKey="review"
              className="h-[calc(100vh-330px)] min-h-[480px]"
              left={<DocumentPane app={app} type="resume" />}
              right={<DocumentPane app={app} type="cover_letter" />}
            />
          </TabsContent>
          <TabsContent value="files">
            <FilesPanel app={app} />
          </TabsContent>
          <TabsContent value="match">
            <Card className="p-5">
              {app.match?.details.components ? (
                <div className="grid gap-6 lg:grid-cols-2">
                  <div>
                    <div className="mb-1 flex items-baseline gap-2">
                      <span className="text-3xl font-semibold">{app.match.score?.toFixed(0)}</span>
                      <span className="text-sm text-muted-foreground">match score{app.match.rank ? ` · ranked #${app.match.rank}` : ""}</span>
                    </div>
                    <p className="mb-3 text-sm text-muted-foreground">{app.match.details.explanation}</p>
                    <ScoreBreakdown components={app.match.details.components} />
                  </div>
                  <div>
                    {(app.match.details.gates ?? []).length > 0 && (
                      <>
                        <h3 className="mb-2 text-sm font-semibold">Hard requirements</h3>
                        <GateList gates={app.match.details.gates ?? []} />
                      </>
                    )}
                    {app.override_reason && (
                      <div className="mt-4 rounded-lg border bg-muted/40 p-3 text-sm">
                        <div className="text-xs font-medium text-muted-foreground">Override reason</div>
                        {app.override_reason}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No match details were recorded for this candidate.</p>
              )}
            </Card>
          </TabsContent>
          <TabsContent value="coverage">
            <CoveragePanel key={`${app.resume?.version}`} applicationId={app.id} />
          </TabsContent>
        </Tabs>
      )}

      <Modal
        open={regenerate !== null}
        onOpenChange={(open) => !open && setRegenerate(null)}
        title={`Regenerate: ${regenerate?.label}`}
        description="Creates a new version. Earlier versions and exported files are kept unchanged."
        className="max-w-xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRegenerate(null)}>
              Cancel
            </Button>
            <Button onClick={() => regenerate && startRegenerate(regenerate)}>
              <Sparkles /> Regenerate
            </Button>
          </>
        }
      >
        <InstructionsField value={instructions} onChange={setInstructions} />
      </Modal>
      <Modal
        open={outcome !== null}
        onOpenChange={(open) => !open && setOutcome(null)}
        title={outcome === "SUBMITTED" ? "Record that this application was submitted?" : `Record outcome: ${outcome?.toLowerCase()}?`}
        description={outcome === "SUBMITTED" ? "ResumeFlow never submits applications. Only record this after you or the candidate actually applied." : "Outcomes can't be undone."}
        footer={
          <>
            <Button variant="ghost" onClick={() => setOutcome(null)}>
              Cancel
            </Button>
            <Button
              onClick={() =>
                outcome &&
                record.mutate(outcome, {
                  onSuccess: () => {
                    toast.success("Outcome recorded");
                    setOutcome(null);
                  },
                  onError: onError("Couldn't record outcome"),
                })
              }
            >
              Record
            </Button>
          </>
        }
      />
    </div>
  );

  function startRegenerate(option: Regenerate) {
    setRegenerate(null);
    generate.mutate({ ...option, instructions }, { onSuccess: () => toast.success("Generating a new version"), onError: onError("Couldn't regenerate") });
  }
}

function DocumentPane({ app, type }: { app: ApplicationDetail; type: DocType }) {
  const latest = (type === "resume" ? app.resume : app.cover_letter) as DocView<ResumeContent | CoverLetterContent> | null;
  const versions = app.versions[type];
  const [selected, setSelected] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ResumeContent | CoverLetterContent | null>(null);
  const viewingOld = selected !== null && latest !== null && selected !== latest.version;
  const old = useQuery({
    queryKey: ["document-version", app.id, type, selected],
    queryFn: () => api.applications.documentVersion(app.id, type, selected!),
    enabled: viewingOld,
  });
  const save = useApplicationMutation(app.id, (content: ResumeContent | CoverLetterContent) => api.applications.editDocument(app.id, type, content));
  const editable = ["AWAITING_REVIEW", "APPROVED", "EXPORTED"].includes(app.status);
  const title = type === "resume" ? "Resume" : "Cover letter";

  useEffect(() => {
    setSelected(null);
  }, [latest?.version]);

  useEffect(() => {
    if (!editing) return;
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && draft) {
        event.preventDefault();
        submit();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const shown = viewingOld ? old.data : latest;
  function submit() {
    if (!draft) return;
    save.mutate(draft, {
      onSuccess: (detail) => {
        const doc = type === "resume" ? detail.resume : detail.cover_letter;
        toast.success(`Saved as version ${doc?.version}`);
        setEditing(false);
        setDraft(null);
      },
      onError: (error) => toast.error("Couldn't save", { description: describeError(error) }),
    });
  }

  let body: ReactNode;
  if (!latest) body = <p className="p-6 text-sm text-muted-foreground">Not generated yet.</p>;
  else if (viewingOld && old.isPending) body = <Skeleton className="m-4 h-96" />;
  else if (editing && draft)
    body = type === "resume" ? <ResumeEditor value={draft as ResumeContent} onChange={setDraft} /> : <CoverLetterEditor value={draft as CoverLetterContent} onChange={setDraft} />;
  else if (shown)
    body =
      type === "resume" ? (
        <ResumePreview content={shown.content as ResumeContent} issues={[]} />
      ) : (
        <CoverLetterPreview content={shown.content as CoverLetterContent} issues={[]} />
      );

  return (
    <Card className="flex h-full flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <span className="text-sm font-semibold">{title}</span>
        {latest && versions.length > 0 && (
          <NativeSelect
            className="w-40"
            value={String(selected ?? latest.version)}
            onChange={(e) => setSelected(Number(e.target.value))}
            disabled={editing}
            aria-label={`${title} version`}
          >
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version} · {v.status}
                {v.origin ? ` · ${v.origin}` : ""}
              </option>
            ))}
          </NativeSelect>
        )}
        {shown?.mode && (
          <Badge variant="outline">
            {shown.mode === "ai" ? (
              <>
                <Sparkles /> AI
              </>
            ) : shown.mode === "edited" ? (
              "Edited"
            ) : (
              "Deterministic"
            )}
          </Badge>
        )}
        {type === "cover_letter" && shown && <span className="text-xs text-muted-foreground tabular-nums">{wordCount(shown.content as CoverLetterContent)} words</span>}
        <div className="ml-auto flex gap-1">
          {editing ? (
            <>
              <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(null); }}>
                <X /> Cancel
              </Button>
              <Button size="sm" onClick={submit} disabled={save.isPending}>
                <Check /> Save version
              </Button>
            </>
          ) : (
            latest &&
            editable &&
            !viewingOld && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setDraft(structuredClone(latest.content));
                  setEditing(true);
                }}
              >
                <Pencil /> Edit
              </Button>
            )
          )}
        </div>
      </div>
      {viewingOld && <div className="border-b bg-muted/60 px-3 py-1.5 text-xs text-muted-foreground">Viewing an earlier version (read-only).</div>}
      {editing && app.status !== "AWAITING_REVIEW" && (
        <div className="border-b bg-warning/10 px-3 py-1.5 text-xs">Saving sends this application back to review; the approved version is kept.</div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/40 p-4">{body}</div>
    </Card>
  );
}

const FILE_LABEL: Record<OutputFile["type"], string> = {
  resume: "Resume",
  cover_letter: "Cover letter",
  job_description: "Job description",
  job_url: "Job URL",
  metadata: "Metadata",
};

function FilesPanel({ app }: { app: ApplicationDetail }) {
  const open = (path: string, reveal = false) => api.system.open(path, reveal).catch((e) => toast.error("Couldn't open", { description: describeError(e) }));
  if (!app.files.length)
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        {app.status === "APPROVED" ? "Approved. Export to write the DOCX, PDF, JD, URL and metadata files." : "Files are written when the approved application is exported."}
      </Card>
    );
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
        <FolderOpen className="size-4 text-muted-foreground" aria-hidden />
        <code className="min-w-0 flex-1 truncate text-xs">{app.output_directory}</code>
        {app.output_directory && (
          <Button size="sm" variant="outline" onClick={() => open(app.output_directory!)}>
            Open folder
          </Button>
        )}
      </div>
      <ul className="divide-y">
        {app.files.map((file) => (
          <li key={file.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
            <Badge variant="outline" className="w-12 justify-center uppercase">
              {file.format}
            </Badge>
            <span className="w-32 shrink-0">{FILE_LABEL[file.type]}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{file.path?.split(/[\\/]/).pop()}</span>
            {file.exists ? (
              <>
                <Button size="sm" variant="ghost" onClick={() => file.path && open(file.path)}>
                  Open
                </Button>
                <Button size="sm" variant="ghost" onClick={() => file.path && open(file.path, true)}>
                  Show in folder
                </Button>
              </>
            ) : (
              <span className="text-xs text-destructive">Missing on disk</span>
            )}
          </li>
        ))}
      </ul>
      <div className="border-t px-4 py-2.5 text-xs text-muted-foreground">
        PDF: {app.pdf_status === "succeeded" ? "converted" : app.pdf_status === "skipped" ? "skipped (converter set to none)" : app.pdf_status === "failed" ? "failed — retry from the banner above" : "pending"}
      </div>
    </Card>
  );
}
