import { CircleAlert, Info, TriangleAlert } from "lucide-react";

import type { Issue } from "@/lib/types";
import { cn } from "@/lib/utils";

const ICON = { error: CircleAlert, warning: TriangleAlert, info: Info };
const COLOR = { error: "text-destructive", warning: "text-warning", info: "text-muted-foreground" };
const ORDER = { error: 0, warning: 1, info: 2 };

export function IssueIcon({ severity, className }: { severity: Issue["severity"]; className?: string }) {
  const Icon = ICON[severity];
  return <Icon className={cn("size-3.5 shrink-0", COLOR[severity], className)} aria-label={severity} />;
}

export function IssueList({ issues, empty = "No issues found." }: { issues: Issue[]; empty?: string }) {
  if (!issues.length) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="space-y-2">
      {[...issues]
        .sort((a, b) => ORDER[a.severity] - ORDER[b.severity])
        .map((issue, index) => (
          <li key={`${issue.code}-${index}`} className="flex gap-2 text-sm">
            <IssueIcon severity={issue.severity} className="mt-0.5" />
            <span>
              {issue.message}
              {issue.location && <span className="ml-1 text-xs text-muted-foreground">({issue.location})</span>}
            </span>
          </li>
        ))}
    </ul>
  );
}

/** Issues attached to one location, e.g. "experience[0].bullets[2]" or "paragraphs[1]". */
export function issuesAt(issues: Issue[], location: string) {
  return issues.filter((i) => i.location === location);
}

export function IssueCounts({ counts }: { counts: { error: number; warning: number; info?: number } }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs">
      {counts.error > 0 && (
        <span className="inline-flex items-center gap-1 text-destructive">
          <IssueIcon severity="error" /> {counts.error}
        </span>
      )}
      {counts.warning > 0 && (
        <span className="inline-flex items-center gap-1 text-warning">
          <IssueIcon severity="warning" /> {counts.warning}
        </span>
      )}
      {counts.error === 0 && counts.warning === 0 && <span className="text-success">No issues</span>}
    </span>
  );
}
