import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Lightbulb } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { CoverageGroup } from "@/lib/types";

function Group({ label, group }: { label: string; group: CoverageGroup }) {
  if (!group.total) return null;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2 text-sm">
        <span className="font-medium">{label}</span>
        <span className="tabular-nums text-muted-foreground">
          {group.in_resume}/{group.total} on this resume · {group.confirmed} in the profile
          {group.in_original_resume !== null && ` · ${group.in_original_resume} in the original resume`}
        </span>
      </div>
      {group.missing.length > 0 && <div className="text-xs text-muted-foreground">Not covered: {group.missing.join(", ")}</div>}
    </div>
  );
}

/** VAL-09/10: job-skill coverage before and after, quality notes, and what changed against the imported resume. */
export function CoveragePanel({ applicationId }: { applicationId: string }) {
  const report = useQuery({ queryKey: ["coverage", applicationId], queryFn: () => api.applications.coverage(applicationId) });
  if (report.isPending) return <Skeleton className="h-64" />;
  if (report.isError) return <p className="text-sm text-muted-foreground">The coverage report isn't available for this application.</p>;
  const { coverage, changes } = report.data;
  const added = changes.bullets.filter((b) => b.kind === "added");
  const rewritten = changes.bullets.filter((b) => b.kind === "rewritten");
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-sm font-semibold">Job skill coverage</h3>
          <p className="text-xs text-muted-foreground">{coverage.note}</p>
        </div>
        <div className="flex items-center gap-3" aria-label="Coverage before and after">
          <div className="text-center">
            <div className="text-2xl font-semibold tabular-nums">{coverage.original_coverage_pct ?? "—"}%</div>
            <div className="text-[11px] text-muted-foreground">original resume</div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" aria-hidden />
          <div className="text-center">
            <div className="text-2xl font-semibold tabular-nums">{coverage.keyword_coverage_pct ?? "—"}%</div>
            <div className="text-[11px] text-muted-foreground">this resume</div>
          </div>
          <div className="ml-auto text-right text-xs text-muted-foreground">
            {coverage.confirmed_coverage_pct ?? "—"}% confirmed in the profile
          </div>
        </div>
        <Group label="Required" group={coverage.required} />
        <Group label="Preferred" group={coverage.preferred} />
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge variant={coverage.unsupported_claims ? "destructive" : "outline"}>{coverage.unsupported_claims} unsupported claim{coverage.unsupported_claims === 1 ? "" : "s"}</Badge>
          <Badge variant={coverage.duplicates.length ? "warning" : "outline"}>{coverage.duplicates.length} near-duplicate bullet pair{coverage.duplicates.length === 1 ? "" : "s"}</Badge>
        </div>
        {coverage.suggestions.length > 0 && (
          <div>
            <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Lightbulb className="size-3.5" aria-hidden /> To improve
            </div>
            <ul className="list-disc space-y-1 pl-5 text-sm" aria-label="Suggested improvements">
              {coverage.suggestions.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        )}
      </Card>
      <Card className="space-y-4 p-5">
        <h3 className="text-sm font-semibold">Changes from the original resume</h3>
        <div className="space-y-1 text-sm">
          {changes.skills.added.length > 0 && <div><span className="font-medium text-success">Skills added:</span> {changes.skills.added.join(", ")}</div>}
          {changes.skills.not_used.length > 0 && <div className="text-muted-foreground">Skills left out: {changes.skills.not_used.join(", ")}</div>}
        </div>
        {added.length > 0 && (
          <div>
            <div className="mb-1 text-xs font-medium text-muted-foreground">Bullets from confirmed experience</div>
            <ul className="space-y-1.5 text-sm">
              {added.map((b) => (
                <li key={b.text} className="rounded-md border-l-2 border-success bg-success/5 px-2 py-1">
                  {b.text} <span className="text-xs text-muted-foreground">— {b.role}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {rewritten.length > 0 && (
          <details>
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">{rewritten.length} bullet{rewritten.length === 1 ? "" : "s"} reworded for this job</summary>
            <ul className="mt-2 space-y-2 text-sm">
              {rewritten.map((b) => (
                <li key={b.text}>
                  <div>{b.text}</div>
                  {b.original && <div className="text-xs text-muted-foreground line-through">{b.original}</div>}
                </li>
              ))}
            </ul>
          </details>
        )}
        {changes.not_used_bullets.length > 0 && (
          <details>
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">{changes.not_used_bullets.length} profile bullet{changes.not_used_bullets.length === 1 ? "" : "s"} not used for this job</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
              {changes.not_used_bullets.map((b) => (
                <li key={b.text}>{b.text}</li>
              ))}
            </ul>
          </details>
        )}
        {changes.previous && (
          <div className="text-xs text-muted-foreground">
            Since the previous version: {changes.previous.bullets_added.length} bullet(s) added, {changes.previous.bullets_removed.length} removed
            {changes.previous.skills_added.length > 0 && `; skills added: ${changes.previous.skills_added.join(", ")}`}
            {changes.previous.skills_removed.length > 0 && `; skills removed: ${changes.previous.skills_removed.join(", ")}`}.
          </div>
        )}
      </Card>
    </div>
  );
}
