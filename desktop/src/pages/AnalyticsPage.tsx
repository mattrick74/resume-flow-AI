import { Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ColumnChart } from "@/components/charts/ColumnChart";
import { ErrorState } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAnalytics } from "@/lib/queries";
import type { Analytics } from "@/lib/types";
import { formatCurrency, formatDuration } from "@/lib/utils";

const OPERATION_LABEL: Record<string, string> = {
  jd_analysis: "Job analysis",
  semantic_match: "Semantic matching",
  resume_generation: "Resume writing",
  cover_letter_generation: "Cover letter writing",
  claim_validation: "Fact-checking",
  resume_parse: "Resume extraction",
};

function shortDate(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function AnalyticsPage() {
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const analytics = useAnalytics(Number(days));
  return (
    <>
      <PageHeader
        title="Analytics"
        description="Measured from recorded runs. Costs are estimates from your model price settings."
        actions={
          <Segmented<"7" | "30" | "90">
            ariaLabel="Time range"
            value={days}
            onValueChange={setDays}
            options={[
              { value: "7", label: "7 days" },
              { value: "30", label: "30 days" },
              { value: "90", label: "90 days" },
            ]}
          />
        }
      />
      {analytics.isPending ? (
        <Skeleton className="h-[600px]" />
      ) : analytics.isError ? (
        <ErrorState error={analytics.error} onRetry={() => void analytics.refetch()} />
      ) : (
        <AnalyticsView data={analytics.data} />
      )}
    </>
  );
}

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </Card>
  );
}

function ChartCard({ title, description, chart, table }: { title: string; description: string; chart: ReactNode; table: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          <Table2 /> {asTable ? "Chart" : "Table"}
        </Button>
      </CardHeader>
      <CardContent>{asTable ? <div className="max-h-64 overflow-y-auto">{table}</div> : chart}</CardContent>
    </Card>
  );
}

function AnalyticsView({ data }: { data: Analytics }) {
  const activeDays = data.days.filter((d) => d.generated > 0).length || 1;
  const budget = data.budget;
  return (
    <div className="space-y-4">
      {(budget.daily_exceeded || budget.monthly_exceeded) && (
        <Alert variant="destructive" title="AI budget reached">
          New AI work falls back to deterministic tailoring until the budget resets or you raise it in Settings.
        </Alert>
      )}
      {!budget.daily_exceeded && !budget.monthly_exceeded && (budget.daily_warning || budget.monthly_warning) && (
        <Alert variant="warning" title="Approaching your AI budget">
          {budget.daily_warning && `Today ${formatCurrency(budget.today_usd)} of ${formatCurrency(budget.daily_budget_usd ?? 0)}. `}
          {budget.monthly_warning && `This month ${formatCurrency(budget.month_usd)} of ${formatCurrency(budget.monthly_budget_usd ?? 0)}.`}
        </Alert>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Generated" value={data.totals.generated.toLocaleString()} sub={`${(data.totals.generated / activeDays).toFixed(1)} per active day · goal ${data.totals.daily_goal}/day`} />
        <Stat label="Exported" value={data.totals.exported.toLocaleString()} sub={`${data.totals.applications.toLocaleString()} applications created`} />
        <Stat label="AI spend" value={formatCurrency(data.totals.cost_usd)} sub={data.totals.cost_per_export_usd !== null ? `${formatCurrency(data.totals.cost_per_export_usd)} per exported application` : "No exports yet"} />
        <Stat
          label="Queue reliability"
          value={`${Math.round((1 - data.queue.retry_rate) * 100)}%`}
          sub={`${data.queue.jobs} jobs · ${data.queue.retried} retried · ${data.queue.failed} failed`}
        />
      </div>
      {data.experience && (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Confirmed experience">
          <Stat
            label="Missing skills answered"
            value={(data.experience.skills_confirmed + data.experience.skills_rejected + data.experience.skills_unsure).toLocaleString()}
            sub={`${data.experience.gaps_identified} identified · ${data.experience.skills_confirmed} used · ${data.experience.skills_rejected} not used · ${data.experience.skills_unsure} unsure`}
          />
          <Stat
            label="Bullets approved"
            value={data.experience.bullets_approved.toLocaleString()}
            sub={`${data.experience.bullets_suggested} drafted from confirmed work`}
          />
          <Stat
            label="Jobs waiting for answers"
            value={(data.experience.jobs_paused - data.experience.jobs_resumed).toLocaleString()}
            sub={`${data.experience.jobs_resumed} continued · ${data.experience.matches_rescored} matches re-scored${data.experience.avg_minutes_to_answer !== null ? ` · ${data.experience.avg_minutes_to_answer} min to answer on average` : ""}`}
          />
          <Stat
            label="Job skill coverage"
            value={data.experience.coverage_after_pct !== null ? `${data.experience.coverage_after_pct}%` : "—"}
            sub={data.experience.coverage_before_pct !== null ? `${data.experience.coverage_before_pct}% in the original resumes` : "Average across tailored resumes"}
          />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard
          title="Applications generated per day"
          description="First generation of each application"
          chart={
            <ColumnChart
              ariaLabel="Applications generated per day"
              data={data.days.map((d) => ({ key: d.date, label: shortDate(d.date), value: d.generated }))}
              format={(v) => Math.round(v).toLocaleString()}
              reference={{ value: data.totals.daily_goal, label: `Goal ${data.totals.daily_goal}` }}
            />
          }
          table={<DaysTable data={data} />}
        />
        <ChartCard
          title="AI spend per day"
          description="Estimated from token usage"
          chart={
            <ColumnChart
              ariaLabel="AI spend per day"
              data={data.days.map((d) => ({ key: d.date, label: shortDate(d.date), value: d.cost_usd }))}
              format={(v) => formatCurrency(v)}
            />
          }
          table={<DaysTable data={data} />}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Cost by model</CardTitle>
            <CardDescription>Tokens and estimated spend</CardDescription>
          </CardHeader>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">Runs</TableHead>
                <TableHead className="text-right">Input tokens</TableHead>
                <TableHead className="text-right">Output tokens</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.by_model.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">
                    No AI runs in this period.
                  </TableCell>
                </TableRow>
              )}
              {data.by_model.map((m) => (
                <TableRow key={m.model}>
                  <TableCell className="font-medium">{m.model}</TableCell>
                  <TableCell className="text-right tabular-nums">{m.runs}</TableCell>
                  <TableCell className="text-right tabular-nums">{m.input_tokens.toLocaleString()}</TableCell>
                  <TableCell className="text-right tabular-nums">{m.output_tokens.toLocaleString()}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCurrency(m.cost_usd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Latency and failures by step</CardTitle>
            <CardDescription>Average and 95th-percentile duration per AI call</CardDescription>
          </CardHeader>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Step</TableHead>
                <TableHead className="text-right">Runs</TableHead>
                <TableHead className="text-right">Avg</TableHead>
                <TableHead className="text-right">p95</TableHead>
                <TableHead className="text-right">Failures</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.by_operation.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">
                    No AI runs in this period.
                  </TableCell>
                </TableRow>
              )}
              {data.by_operation.map((op) => (
                <TableRow key={op.operation}>
                  <TableCell className="font-medium">{OPERATION_LABEL[op.operation] ?? op.operation}</TableCell>
                  <TableCell className="text-right tabular-nums">{op.runs}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatDuration(op.avg_duration_ms)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatDuration(op.p95_duration_ms)}</TableCell>
                  <TableCell className="text-right tabular-nums">{op.failures > 0 ? `${op.failures} (${Math.round(op.failure_rate * 100)}%)` : "0"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      </div>
    </div>
  );
}

function DaysTable({ data }: { data: Analytics }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Date</TableHead>
          <TableHead className="text-right">Generated</TableHead>
          <TableHead className="text-right">Exported</TableHead>
          <TableHead className="text-right">AI runs</TableHead>
          <TableHead className="text-right">Cost</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {[...data.days].reverse().map((d) => (
          <TableRow key={d.date}>
            <TableCell>{shortDate(d.date)}</TableCell>
            <TableCell className="text-right tabular-nums">{d.generated}</TableCell>
            <TableCell className="text-right tabular-nums">{d.exported}</TableCell>
            <TableCell className="text-right tabular-nums">{d.ai_runs}</TableCell>
            <TableCell className="text-right tabular-nums">{formatCurrency(d.cost_usd)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
