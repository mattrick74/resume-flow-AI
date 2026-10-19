import {
  Bookmark,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  ClipboardCheck,
  Clock,
  FilePlus2,
  Inbox,
  Layers,
  LoaderCircle,
  Sparkles,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { describeError, ErrorState } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { StatusBadge } from "@/components/common/StatusBadge";
import { EmptyState } from "@/components/common/EmptyState";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboardSummary, useProcessJobs, useSettings } from "@/lib/queries";
import type { DashboardSummary } from "@/lib/types";
import { cn, formatCurrency, formatDuration, formatRelativeTime } from "@/lib/utils";

function todayLabel(isoDate?: string) {
  const date = isoDate ? new Date(`${isoDate}T00:00:00`) : new Date();
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

export function DashboardPage() {
  const summary = useDashboardSummary();

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={todayLabel(summary.data?.date)}
        actions={
          <Button asChild>
            <Link to="/jobs/new">
              <FilePlus2 /> New job
            </Link>
          </Button>
        }
      />
      {summary.isPending ? (
        <DashboardSkeleton />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <DashboardContent data={summary.data} />
      )}
    </>
  );
}

function DashboardContent({ data }: { data: DashboardSummary }) {
  const percent = data.daily_goal ? Math.round((data.generated_today / data.daily_goal) * 100) : 0;

  const budget = data.budget;
  return (
    <div className="space-y-4">
      {(budget.daily_exceeded || budget.monthly_exceeded) && (
        <Alert variant="destructive" title="AI budget reached" action={<Button asChild size="sm" variant="outline"><Link to="/settings">Budgets</Link></Button>}>
          New documents use deterministic tailoring (profile facts only, no AI) until the budget resets or is raised.
        </Alert>
      )}
      {!budget.daily_exceeded && !budget.monthly_exceeded && (budget.daily_warning || budget.monthly_warning) && (
        <Alert variant="warning" title="Approaching your AI budget">
          {budget.daily_warning && `Today: ${formatCurrency(budget.today_usd)} of ${formatCurrency(budget.daily_budget_usd ?? 0)}. `}
          {budget.monthly_warning && `This month: ${formatCurrency(budget.month_usd)} of ${formatCurrency(budget.monthly_budget_usd ?? 0)}.`}
        </Alert>
      )}
      {data.saved_jobs > 0 && <SavedJobsBanner count={data.saved_jobs} />}
      {(data.jobs_needing_confirmation ?? 0) > 0 && (
        <Alert
          variant="warning"
          title={`${data.jobs_needing_confirmation} job${data.jobs_needing_confirmation === 1 ? "" : "s"} wait for answers about missing skills`}
          action={
            <Button asChild size="sm" variant="outline">
              <Link to="/jobs?filter=answers&answer=1">Answer now</Link>
            </Button>
          }
        >
          The job asks for skills the top candidate's profile doesn't show. Answer once for all of them; generation continues automatically.
        </Alert>
      )}
      {data.jobs_failed > 0 && (
        <Alert variant="warning" title={`${data.jobs_failed} job${data.jobs_failed === 1 ? "" : "s"} couldn't be processed`} action={<Button asChild size="sm" variant="outline"><Link to="/jobs?filter=failed">Review and retry</Link></Button>}>
          Automatic processing stopped before documents were generated.
        </Alert>
      )}
      {data.processing_failed > 0 && (
        <Alert variant="warning" title={`${data.processing_failed} background task${data.processing_failed === 1 ? "" : "s"} failed`} action={<Button asChild size="sm" variant="outline"><Link to="/review">Open review queue</Link></Button>}>
          Failed generations and PDF conversions can be retried from the application.
        </Alert>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card className="md:col-span-2">
          <CardHeader>
            <CardDescription>Today’s goal</CardDescription>
            <div className="flex items-baseline gap-2">
              <span className="text-3xl font-semibold tabular-nums tracking-tight">{data.generated_today}</span>
              <span className="text-sm text-muted-foreground">of {data.daily_goal} applications generated</span>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={percent} aria-label="Daily goal progress" />
            <div className="flex gap-6 text-xs text-muted-foreground">
              <span>
                <span className="font-medium text-foreground tabular-nums">{data.jobs_today}</span> jobs received
              </span>
              <span>
                <span className="font-medium text-foreground tabular-nums">{data.exported_today}</span> exported
              </span>
              <span>
                <span className="font-medium text-foreground tabular-nums">{percent}%</span> of goal
              </span>
            </div>
          </CardContent>
        </Card>
        <StatCard
          icon={ClipboardCheck}
          label="Awaiting review"
          value={data.awaiting_review}
          tone={data.awaiting_review > 0 ? "warning" : undefined}
          footer={<Link to="/review" className="hover:text-foreground hover:underline">Open review queue</Link>}
        />
        <StatCard
          icon={CircleAlert}
          label="Failed today"
          value={data.failed_today}
          tone={data.failed_today > 0 ? "destructive" : undefined}
          footer="Retry from the application record"
        />
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard icon={Wallet} label="AI spend today" value={formatCurrency(data.cost_today_usd)} footer="Estimated until billed" />
        <StatCard icon={Clock} label="Avg. generation time" value={formatDuration(data.avg_generation_ms)} footer="Across today’s AI runs" />
        <StatCard icon={Layers} label="Queue" value={data.queue_depth} footer="Queued or running jobs" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Latest application updates</CardDescription>
          </CardHeader>
          {data.recent_activity.length === 0 ? (
            <EmptyState
              icon={Inbox}
              title="No applications yet"
              description="Once candidates are imported, paste a job description to rank them and generate a tailored resume and cover letter."
              actions={
                <Button variant="outline" size="sm" asChild>
                  <Link to="/candidates">View candidates</Link>
                </Button>
              }
            />
          ) : (
            <ul className="divide-y px-5 pb-2">
              {data.recent_activity.map((item) => (
                <li key={item.application_id}>
                  <Link to={`/applications/${item.application_id}`} className="-mx-2 flex items-center gap-3 rounded-md px-2 py-3 text-sm outline-none hover:bg-accent/50 focus-visible:ring-[3px] focus-visible:ring-ring/50">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">{item.candidate_name}</div>
                      <div className="truncate text-xs text-muted-foreground">
                        {[item.title, item.company].filter(Boolean).join(" · ") || "Untitled role"}
                      </div>
                    </div>
                    <StatusBadge status={item.status} />
                    <span className="w-24 text-right text-xs text-muted-foreground">{formatRelativeTime(item.updated_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <SetupChecklist data={data} />
      </div>
    </div>
  );
}

function SavedJobsBanner({ count }: { count: number }) {
  const processJobs = useProcessJobs();
  return (
    <Card className="flex-row flex-wrap items-center gap-3 border-primary/25 bg-accent/40 px-4 py-3">
      <Bookmark className="size-5 text-primary" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">
          {count} saved job{count === 1 ? "" : "s"} waiting
        </div>
        <div className="text-xs text-muted-foreground">One click analyzes each job, picks the top eligible candidate and writes a tailored resume and cover letter for your review.</div>
      </div>
      <Button asChild variant="ghost" size="sm">
        <Link to="/jobs?filter=saved">View</Link>
      </Button>
      <Button
        size="sm"
        disabled={processJobs.isPending}
        onClick={() =>
          processJobs.mutate(
            { all_saved: true },
            {
              onSuccess: (result) => toast.success(`${result.queued.length} job${result.queued.length === 1 ? "" : "s"} queued`, { description: "Finished documents appear in the Review Queue." }),
              onError: (error) => toast.error("Couldn't queue the jobs", { description: describeError(error) }),
            },
          )
        }
      >
        {processJobs.isPending ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Generate all
      </Button>
    </Card>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  footer,
  tone,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  footer?: ReactNode;
  tone?: "warning" | "destructive";
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardDescription>{label}</CardDescription>
        <Icon
          className={cn(
            "size-4 text-muted-foreground",
            tone === "warning" && "text-warning",
            tone === "destructive" && "text-destructive",
          )}
          aria-hidden
        />
      </CardHeader>
      <CardContent className="pt-2">
        <div className="text-2xl font-semibold tabular-nums tracking-tight">{value}</div>
        {footer && <div className="mt-1 text-xs text-muted-foreground">{footer}</div>}
      </CardContent>
    </Card>
  );
}

function SetupChecklist({ data }: { data: DashboardSummary }) {
  const settings = useSettings();
  const keyStored = settings.data?.secrets_configured.openai_api_key ?? false;
  const steps = [
    { label: keyStored ? "OpenAI API key stored" : "Store your OpenAI API key (optional — offline mode works without it)", done: keyStored, to: "/settings" },
    {
      label: data.candidates_total ? `Get candidates ready (${data.candidates_ready} of ${data.candidates_total} ready)` : "Import candidate profiles",
      done: data.candidates_ready > 0 && data.candidates_ready === data.candidates_total,
      to: "/candidates",
    },
    { label: "Paste your first job description", done: data.recent_activity.length > 0, to: "/jobs/new" },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Get set up</CardTitle>
        <CardDescription>Three steps before your first application</CardDescription>
      </CardHeader>
      <CardContent className="pt-3">
        <ol className="space-y-1">
          {steps.map((step) => (
            <li key={step.label}>
              <Link
                to={step.to}
                className="flex items-center gap-3 rounded-md px-2 py-2 text-sm outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
              >
                {step.done ? (
                  <CircleCheck className="size-4 text-success" aria-label="Done" />
                ) : (
                  <CircleDashed className="size-4 text-muted-foreground" aria-label="To do" />
                )}
                <span className={cn(step.done && "text-muted-foreground line-through")}>{step.label}</span>
              </Link>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading dashboard">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Skeleton className="h-36 md:col-span-2" />
        <Skeleton className="h-36" />
        <Skeleton className="h-36" />
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
        <Skeleton className="h-28" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}
