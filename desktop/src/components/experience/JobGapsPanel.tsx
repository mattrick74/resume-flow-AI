import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ListChecks } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { SkillQuestions } from "@/components/experience/SkillQuestions";
import { GateList } from "@/components/jobs/MatchCard";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { cn } from "@/lib/utils";

/**
 * Optional: the job skills the chosen candidate's profile doesn't show, with quick answers. Generation never waits for
 * it; answers only add what the candidate has really done, for this and later jobs.
 */
export function JobGapsPanel({ jobId, candidateId }: { jobId: string; candidateId: string }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const gaps = useQuery({ queryKey: ["skill-gaps", jobId, candidateId], queryFn: () => api.jobs.skillGaps(jobId, candidateId) });
  if (gaps.isPending || gaps.isError) return null;
  const report = gaps.data;
  const missing = report.items.filter((i) => i.status === "plausible" || i.status === "unknown");
  if (!missing.length && !report.pending_suggestions) return null;
  const inSkillsOnly = report.items.filter((i) => i.status === "confirmed");
  const rejected = report.items.filter((i) => i.status === "rejected");
  const name = report.candidate_name.split(" ")[0];
  return (
    <Card className="p-0">
      <button type="button" className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <ListChecks className="size-4 text-muted-foreground" aria-hidden />
        <span className="text-sm font-semibold">
          {missing.length} job skill{missing.length === 1 ? "" : "s"} not in {name}'s profile
        </span>
        <span className="text-xs text-muted-foreground">
          Optional{report.potential_score !== null && report.score !== null && report.potential_score > report.score ? ` · match ${report.score.toFixed(0)} → up to ${report.potential_score.toFixed(0)} if confirmed` : ""}
        </span>
        <ChevronDown className={cn("ml-auto size-4 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open && (
        <div className="space-y-3 border-t px-4 py-3">
          <p className="text-xs text-muted-foreground">
            Generating doesn't wait for these. If {name} has used any of them, say where and what was done: it's added to the profile at once and used from the next generation. Skills left unanswered simply stay off the resume.
          </p>
          <SkillQuestions
            candidateId={report.candidate_id}
            candidateName={report.candidate_name}
            roles={report.roles}
            questions={missing.map((i) => ({ skill: i.skill, normalized: i.normalized, status: i.status, evidence: i.evidence, answer: i.answer, kind: i.kind }))}
            sourceJobIds={[jobId]}
            onDone={() => {
              void gaps.refetch();
              void client.invalidateQueries({ queryKey: queryKeys.job(jobId) });
            }}
          />
          {report.pending_suggestions > 0 && (
            <p className="text-sm">
              {report.pending_suggestions} drafted bullet{report.pending_suggestions === 1 ? "" : "s"} wait for approval on{" "}
              <Link className="text-primary hover:underline" to={`/candidates/${report.candidate_id}?tab=evidence`}>
                {name}'s Evidence tab
              </Link>
              .
            </p>
          )}
          {inSkillsOnly.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Listed under Skills but not shown at any employer: {inSkillsOnly.map((i) => i.skill).join(", ")}. Add where they were used on the Evidence tab for a stronger bullet.
            </p>
          )}
          {rejected.length > 0 && <p className="text-xs text-muted-foreground">Answered “have not used”: {rejected.map((i) => i.skill).join(", ")}.</p>}
          {report.hard_gates.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-muted-foreground">Hard requirements (answers here don't change them)</div>
              <GateList gates={report.hard_gates} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
