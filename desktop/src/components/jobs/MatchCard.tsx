import { ChevronDown, CircleCheck, CircleHelp, CircleX, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ScoreBreakdown } from "@/components/jobs/ScoreBreakdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useHardRequirementGates } from "@/lib/queries";
import type { CandidateMatch, Gate, SkillCheck } from "@/lib/types";
import { cn } from "@/lib/utils";

export function GateList({ gates }: { gates: Gate[] }) {
  if (!gates.length) return <p className="text-xs text-muted-foreground">No hard requirements apply.</p>;
  return (
    <ul className="space-y-1.5">
      {gates.map((gate, index) => (
        <li key={`${gate.name}-${index}`} className="flex items-start gap-2 text-xs">
          {gate.status === "pass" ? (
            <CircleCheck className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="Passes" />
          ) : gate.status === "fail" ? (
            <CircleX className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-label="Fails" />
          ) : (
            <CircleHelp className="mt-0.5 size-3.5 shrink-0 text-warning" aria-label="Unknown" />
          )}
          <span>
            <span className="font-medium">{gate.name}</span>
            <span className="text-muted-foreground"> — {gate.detail}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function SkillPills({ matched, missing }: { matched: SkillCheck[]; missing: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {matched.map((check) => (
        <Tooltip key={check.skill}>
          <TooltipTrigger asChild>
            <span className="inline-flex cursor-default items-center gap-1 rounded-md border border-success/25 bg-success/10 px-2 py-0.5 text-xs">
              {check.method === "semantic" ? <Sparkles className="size-3 text-success" aria-label="AI-confirmed" /> : <CircleCheck className="size-3 text-success" aria-hidden />}
              {check.skill}
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-sm">{check.evidence ?? "Verified skill"}</TooltipContent>
        </Tooltip>
      ))}
      {missing.map((skill) => (
        <span key={skill} className="inline-flex items-center gap-1 rounded-md border border-dashed px-2 py-0.5 text-xs text-muted-foreground">
          <CircleX className="size-3" aria-hidden /> {skill}
        </span>
      ))}
    </div>
  );
}

export function MatchCard({
  match,
  highlight,
  actionLabel,
  onSelect,
  disabled,
}: {
  match: CandidateMatch;
  highlight?: boolean;
  actionLabel: string;
  onSelect: () => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(highlight ?? false);
  const gatesOn = useHardRequirementGates();
  const details = match.details;
  const components = details.components;
  return (
    <Card className={cn("p-4", highlight && "border-primary/40 ring-1 ring-primary/20")}>
      <div className="flex items-start gap-3">
        {match.rank !== null && (
          <div className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg text-sm font-semibold", highlight ? "bg-primary text-primary-foreground" : "bg-muted")}>
            {match.rank}
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/candidates/${match.candidate_id}`} className="font-semibold hover:underline">
              {match.candidate_name}
            </Link>
            {match.eligibility_status === "needs_review" && <Badge variant="warning">Needs check</Badge>}
            {match.eligibility_status === "ineligible" && <Badge variant="destructive">Fails a hard requirement</Badge>}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{details.explanation}</p>
        </div>
        <div className="text-right">
          <div className="text-2xl font-semibold leading-none" title="Verified match score: confirmed evidence only">
            {match.score?.toFixed(0) ?? "—"}
          </div>
          <div className="text-[11px] text-muted-foreground">
            verified
            {match.previous_score != null && match.score != null && Math.round(match.score) !== Math.round(match.previous_score) && (
              <span className={match.score > match.previous_score ? "text-success" : "text-destructive"} aria-label="Change since the last ranking">
                {" "}
                {match.score > match.previous_score ? "▲" : "▼"} from {match.previous_score.toFixed(0)}
              </span>
            )}
          </div>
          {match.potential_score != null && match.score != null && Math.round(match.potential_score) > Math.round(match.score) && (
            <div className="mt-0.5 text-[11px] text-muted-foreground" title="If every unanswered job skill were confirmed. Shown only; never used to choose a candidate.">
              potential {match.potential_score.toFixed(0)}
            </div>
          )}
        </div>
      </div>
      {components && (
        <div className="mt-3">
          <ScoreBreakdown components={components} compact={!open} />
        </div>
      )}
      {open && components && (
        <div className="mt-4 grid gap-4 border-t pt-4 md:grid-cols-2">
          <div className="space-y-3">
            <div>
              <div className="mb-1.5 text-xs font-medium text-muted-foreground">Required skills</div>
              <SkillPills matched={components.mandatory_skills.matched} missing={components.mandatory_skills.missing} />
            </div>
            {(components.preferred_skills.matched.length > 0 || components.preferred_skills.missing.length > 0) && (
              <div>
                <div className="mb-1.5 text-xs font-medium text-muted-foreground">Preferred skills</div>
                <SkillPills matched={components.preferred_skills.matched} missing={components.preferred_skills.missing} />
              </div>
            )}
          </div>
          <div className="space-y-3">
            {gatesOn && (
              <div>
                <div className="mb-1.5 text-xs font-medium text-muted-foreground">Hard requirements</div>
                <GateList gates={details.gates ?? []} />
              </div>
            )}
            {gatesOn && details.manual_checks && details.manual_checks.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">Check manually</div>
                <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                  {details.manual_checks.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <ChevronDown className={cn("transition-transform", open && "rotate-180")} /> {open ? "Hide evidence" : "Show evidence"}
        </Button>
        <Button className="ml-auto" size="sm" variant={highlight ? "default" : "outline"} onClick={onSelect} disabled={disabled}>
          {actionLabel}
        </Button>
      </div>
    </Card>
  );
}
