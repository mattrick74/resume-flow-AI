import { Briefcase, CircleAlert, Clock, FilePlus2, Files, LoaderCircle, MessageCircleQuestion, Search, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/EmptyState";
import { describeError, ErrorState } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { PAGE_SIZES, Pagination } from "@/components/common/Pagination";
import { StatusBadge } from "@/components/common/StatusBadge";
import { BatchConfirmDialog } from "@/components/experience/BatchConfirmDialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { jobsInFlight, useDashboardSummary, useJobs, useProcessJobs } from "@/lib/queries";
import type { JobSummary, QueueResult } from "@/lib/types";
import { formatRelativeTime } from "@/lib/utils";

type Filter = "all" | "saved" | "answers" | "matched" | "none" | "failed";

const FILTER_STATUS: Record<Filter, string | undefined> = {
  all: undefined,
  saved: "DRAFT",
  answers: "MATCHED", // matched jobs waiting for answers about missing skills
  matched: "MATCHED",
  none: "NO_ELIGIBLE_CANDIDATE",
  failed: "FAILED",
};

const isFilter = (value: string | null): value is Filter => value !== null && value in FILTER_STATUS;

/** Jobs that can be (re)queued: not already in the queue and without a live application. */
export function canProcess(job: JobSummary) {
  const active = job.processing?.status === "queued" || job.processing?.status === "running";
  const openApplication = job.application && job.application.status !== "FAILED" && job.application.status !== "REJECTED";
  return !active && !openApplication && job.status !== "ANALYZING";
}

function ProgressCell({ job, onRetry, retrying, onAnswer }: { job: JobSummary; onRetry: () => void; retrying: boolean; onAnswer: () => void }) {
  const processing = job.processing;
  if (job.needs_confirmation && !job.application && processing?.status !== "queued" && processing?.status !== "running") {
    return (
      <Button
        variant="outline"
        size="sm"
        className="h-7 border-warning/50 text-warning"
        onClick={(e) => {
          e.stopPropagation();
          onAnswer();
        }}
        title="Generation waits until the missing job skills are answered"
      >
        <MessageCircleQuestion /> Needs answers
      </Button>
    );
  }
  if (processing?.status === "queued") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-muted-foreground">
        <Clock className="size-4" /> Queued
      </span>
    );
  }
  if (processing?.status === "running" || job.status === "ANALYZING") {
    const progress = processing?.progress ?? 0;
    return (
      <div className="w-36 space-y-1">
        <span className="inline-flex items-center gap-1.5 text-sm">
          <LoaderCircle className="size-4 animate-spin text-primary" /> {progress < 40 ? "Analyzing" : "Matching"}
        </span>
        <Progress value={progress} aria-label="Processing progress" />
      </div>
    );
  }
  if (job.application) {
    const generating = job.application.status === "GENERATING" || job.application.status === "VALIDATING";
    return (
      <Link to={`/applications/${job.application.id}`} onClick={(e) => e.stopPropagation()} className="inline-flex items-center gap-2 text-sm hover:underline">
        {generating ? (
          <>
            <LoaderCircle className="size-4 animate-spin text-primary" /> Writing documents
          </>
        ) : (
          <StatusBadge status={job.application.status} />
        )}
      </Link>
    );
  }
  if (processing?.status === "failed" || job.status === "FAILED") {
    return (
      <div className="flex max-w-56 items-center gap-2">
        <span className="inline-flex min-w-0 items-center gap-1 text-sm text-destructive" title={processing?.error ?? undefined}>
          <CircleAlert className="size-4 shrink-0" /> <span className="truncate">{processing?.error ?? "Failed"}</span>
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 shrink-0"
          disabled={retrying}
          onClick={(e) => {
            e.stopPropagation();
            onRetry();
          }}
        >
          Retry
        </Button>
      </div>
    );
  }
  if (job.status === "DRAFT") return <span className="text-sm text-muted-foreground">Waiting</span>;
  return <span className="text-muted-foreground">—</span>;
}

function describeQueued(result: QueueResult) {
  const queued = result.queued.length;
  const skipped = result.skipped.length;
  const title = queued ? `${queued} job${queued === 1 ? "" : "s"} queued for tailored documents` : "Nothing new was queued";
  const reasons = [...new Set(result.skipped.map((s) => s.reason.toLowerCase()))].join(", ");
  return { title, description: skipped ? `${skipped} skipped (${reasons}).` : "Finished documents are saved automatically and appear in Applications." };
}

export function JobsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const filterParam = params.get("filter");
  const filter: Filter = isFilter(filterParam) ? filterParam : "all";
  const setFilter = (next: Filter) => {
    setSelected(new Set());
    // Back to page 1, keeping the page size.
    setParams((current) => ({ ...(next === "all" ? {} : { filter: next }), ...(current.get("size") ? { size: current.get("size")! } : {}) }), { replace: true });
  };
  // The page and page size live in the address, so coming back from a job returns to the same page.
  const size: number = PAGE_SIZES.find((s) => String(s) === params.get("size")) ?? PAGE_SIZES[0];
  const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
  const setPaging = (nextPage: number, nextSize = size) => {
    setSelected(new Set());
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (nextPage > 1) next.set("page", String(nextPage));
        else next.delete("page");
        if (nextSize !== PAGE_SIZES[0]) next.set("size", String(nextSize));
        else next.delete("size");
        return next;
      },
      { replace: true },
    );
  };
  const onSearch = (value: string) => {
    setSearch(value);
    if (page > 1) setPaging(1);
  };
  const jobs = useJobs({
    status: FILTER_STATUS[filter],
    q: search || undefined,
    needs_answers: filter === "answers",
    limit: size,
    offset: (page - 1) * size,
  });
  const total = jobs.data?.total ?? 0;
  // Past the last page (jobs removed, or an old link): go to the last page there is.
  useEffect(() => {
    if (jobs.data && !jobs.isPlaceholderData && page > 1 && jobs.data.items.length === 0 && total > 0) setPaging(Math.ceil(total / size));
  });
  const summary = useDashboardSummary();
  const processJobs = useProcessJobs();
  const waiting = summary.data?.saved_jobs ?? 0;
  const needAnswers = summary.data?.jobs_needing_confirmation ?? 0;
  const answering = params.get("answer") === "1";
  const setAnswering = (open: boolean) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (open) next.set("answer", "1");
        else next.delete("answer");
        return next;
      },
      { replace: true },
    );

  const visible = jobs.data?.items ?? [];
  const selectable = visible.filter(canProcess);
  const chosen = selectable.filter((job) => selected.has(job.id));
  const allChosen = selectable.length > 0 && chosen.length === selectable.length;

  const toggle = (id: string, on: boolean) =>
    setSelected((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const run = (body: { job_ids?: string[]; all_saved?: boolean }) =>
    processJobs.mutate(body, {
      onSuccess: (result) => {
        const { title, description } = describeQueued(result);
        (result.queued.length ? toast.success : toast.info)(title, { description });
        setSelected(new Set());
        // Queued saved jobs leave the "Saved" view as they start; show everything so progress stays visible.
        if (filter === "saved" && result.queued.length) setFilter("all");
      },
      onError: (error) => toast.error("Couldn't queue the jobs", { description: describeError(error) }),
    });

  return (
    <>
      <PageHeader
        title="Jobs"
        description="Saved and analyzed jobs. Generate tailored resumes and cover letters for many at once; completed files are saved automatically."
        actions={
          <>
            <Button asChild variant="outline">
              <Link to="/jobs/new?mode=batch">
                <Files /> Add several
              </Link>
            </Button>
            {needAnswers > 0 && (
              <Button variant="outline" onClick={() => setAnswering(true)}>
                <MessageCircleQuestion /> Answer missing skills ({needAnswers})
              </Button>
            )}
            <Button disabled={!waiting || processJobs.isPending} onClick={() => run({ all_saved: true })}>
              {processJobs.isPending ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Generate all saved ({waiting})
            </Button>
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented<Filter>
          ariaLabel="Job status"
          value={filter}
          onValueChange={setFilter}
          options={[
            { value: "all", label: "All" },
            { value: "saved", label: "Saved" },
            { value: "answers", label: `Needs answers${needAnswers ? ` (${needAnswers})` : ""}` },
            { value: "matched", label: "Matched" },
            { value: "none", label: "No eligible candidate" },
            { value: "failed", label: "Failed" },
          ]}
        />
        <div className="relative w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input value={search} onChange={(e) => onSearch(e.target.value)} placeholder="Search title, company or URL" className="pl-8" aria-label="Search jobs" />
        </div>
        {jobsInFlight(jobs.data?.items) && (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <LoaderCircle className="size-3.5 animate-spin" /> Working in the background — you can keep using the app
          </span>
        )}
      </div>

      {chosen.length > 0 && (
        <Card className="mb-3 flex-row flex-wrap items-center gap-3 px-4 py-2.5" role="region" aria-label="Selection">
          <span className="text-sm">
            <span className="font-semibold tabular-nums">{chosen.length}</span> selected
          </span>
          <Button size="sm" disabled={processJobs.isPending} onClick={() => run({ job_ids: chosen.map((j) => j.id) })}>
            <Sparkles /> Generate for {chosen.length} selected
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Clear selection
          </Button>
        </Card>
      )}

      {jobs.isPending ? (
        <Skeleton className="h-72" />
      ) : jobs.isError ? (
        <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
      ) : visible.length === 0 ? (
        <Card>
          <EmptyState
            icon={Briefcase}
            title={filter === "saved" ? "No saved jobs" : search ? "No jobs match your search" : filter === "all" ? "No jobs yet" : "No jobs here"}
            description={
              filter === "saved"
                ? "Save job descriptions for later from New Job → Several jobs, then generate them all with one click."
                : "Paste job descriptions to extract their requirements and rank your candidates."
            }
            actions={
              <Button asChild variant="outline" size="sm">
                <Link to={filter === "saved" ? "/jobs/new?mode=batch" : "/jobs/new"}>
                  <FilePlus2 /> {filter === "saved" ? "Add several jobs" : "New job"}
                </Link>
              </Button>
            }
            className="py-16"
          />
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={allChosen ? true : chosen.length ? "indeterminate" : false}
                    disabled={!selectable.length}
                    onCheckedChange={(value) => setSelected(value === true ? new Set(selectable.map((j) => j.id)) : new Set())}
                    aria-label="Select all jobs that can be generated"
                  />
                </TableHead>
                <TableHead>Job</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Tailored documents</TableHead>
                <TableHead>Top candidate</TableHead>
                <TableHead className="text-right">Eligible</TableHead>
                <TableHead className="text-right">Added</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((job) => {
                const processable = canProcess(job);
                const label = job.title ?? "Untitled job";
                return (
                  <TableRow key={job.id} className="cursor-pointer" data-state={selected.has(job.id) && processable ? "selected" : undefined} onClick={() => navigate(`/jobs/${job.id}`)}>
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      {processable && <Checkbox checked={selected.has(job.id)} onCheckedChange={(value) => toggle(job.id, value === true)} aria-label={`Select ${label}`} />}
                    </TableCell>
                    <TableCell>
                      <Link to={`/jobs/${job.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                        {label}
                      </Link>
                      <div className="text-xs text-muted-foreground">{[job.company, job.location].filter(Boolean).join(" · ") || "—"}</div>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={job.status} subject="job" />
                    </TableCell>
                    <TableCell>
                      <ProgressCell job={job} retrying={processJobs.isPending} onRetry={() => run({ job_ids: [job.id] })} onAnswer={() => setAnswering(true)} />
                    </TableCell>
                    <TableCell>{job.top_candidate ?? <span className="text-muted-foreground">—</span>}</TableCell>
                    <TableCell className="text-right tabular-nums">{job.status === "DRAFT" ? "—" : (job.eligible_count ?? 0)}</TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground">{formatRelativeTime(job.created_at)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Card>
      )}
      {total > 0 && <Pagination page={page} pageSize={size} total={total} onPage={(next) => setPaging(next)} onPageSize={(next) => setPaging(1, next)} label="jobs" />}
      <BatchConfirmDialog open={answering} onOpenChange={setAnswering} />
    </>
  );
}
