import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, CircleX, LoaderCircle, Send } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { SuggestionsReview } from "@/components/experience/SuggestionsReview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { ConfirmResult, ExperienceRecord, GapStatus, RoleOption, SkillAnswer, SkillDecision } from "@/lib/types";

/** One job skill to ask about: from one job's gap report or aggregated across a batch. */
export interface SkillQuestion {
  skill: string;
  normalized: string;
  status: GapStatus;
  evidence: string | null;
  answer: SkillDecision | null;
  kind?: "required" | "preferred";
  required_count?: number;
  preferred_count?: number;
}

interface Draft {
  decision: SkillDecision | null;
  role_ids: string[];
  context: string;
  project: string;
  outcome: string;
}

const EMPTY: Draft = { decision: null, role_ids: [], context: "", project: "", outcome: "" };

const DECISIONS: { value: SkillDecision; label: string; icon: typeof CircleCheck }[] = [
  { value: "used", label: "Have used", icon: CircleCheck },
  { value: "not_used", label: "Have not used", icon: CircleX },
  { value: "unsure", label: "Unsure", icon: CircleHelp },
];

function demand(q: SkillQuestion) {
  if (q.required_count !== undefined) {
    const parts = [q.required_count && `required by ${q.required_count} job${q.required_count === 1 ? "" : "s"}`, q.preferred_count && `preferred by ${q.preferred_count}`];
    return parts.filter(Boolean).join(" · ");
  }
  return q.kind === "required" ? "Required" : "Preferred";
}

export function toAnswer(question: SkillQuestion, draft: Draft): SkillAnswer {
  const used = draft.decision === "used";
  return {
    skill: question.skill,
    decision: draft.decision as SkillDecision,
    role_ids: used ? draft.role_ids : [],
    context: used && draft.role_ids.length ? draft.context.trim() || null : null,
    project: used && draft.role_ids.length ? draft.project.trim() || null : null,
    outcome: used && draft.role_ids.length ? draft.outcome.trim() || null : null,
  };
}

/**
 * Quick confirmation (v1.8 CAN-14/15): Have used / Have not used / Unsure for each missing job skill. "Have used" can
 * be tied to the roles where it happened, with a few words about the work; only those become a bullet, after approval.
 */
export function SkillQuestions({
  candidateId,
  candidateName,
  roles,
  questions,
  sourceJobIds,
  onDone,
}: {
  candidateId: string;
  candidateName: string;
  roles: RoleOption[];
  questions: SkillQuestion[];
  sourceJobIds: string[];
  onDone?: (result: ConfirmResult | null) => void;
}) {
  const client = useQueryClient();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<ExperienceRecord[] | null>(null);
  const draft = (key: string) => drafts[key] ?? EMPTY;
  const set = (key: string, changes: Partial<Draft>) => setDrafts((all) => ({ ...all, [key]: { ...(all[key] ?? EMPTY), ...changes } }));
  const answered = questions.filter((q) => draft(q.normalized).decision);
  const missingContext = answered.filter((q) => {
    const d = draft(q.normalized);
    return d.decision === "used" && d.role_ids.length > 0 && !d.context.trim();
  });

  const refresh = () => {
    for (const key of [queryKeys.jobs, queryKeys.dashboard, queryKeys.candidate(candidateId), ["skill-gaps"], ["batch-gaps"], ["evidence", candidateId], queryKeys.applications]) {
      void client.invalidateQueries({ queryKey: key });
    }
    // Re-ranking runs in the background; refresh again once it has finished.
    window.setTimeout(() => void client.invalidateQueries({ queryKey: queryKeys.jobs }), 2_500);
  };

  const submit = async () => {
    setBusy(true);
    try {
      const result = await api.candidates.confirmSkills(
        candidateId,
        answered.map((q) => toAnswer(q, draft(q.normalized))),
        sourceJobIds,
      );
      client.setQueryData(queryKeys.candidate(candidateId), result.candidate);
      refresh();
      const warnings = result.answers.flatMap((a) => a.issues.map((i) => i.message));
      const added = result.approved?.length ?? 0;
      toast.success(`${result.answers.length} answer${result.answers.length === 1 ? "" : "s"} saved for ${candidateName}`, {
        description:
          [
            added ? `${added} bullet${added === 1 ? "" : "s"} added from your descriptions.` : "",
            result.suggestions.length ? `${result.suggestions.length} bullet${result.suggestions.length === 1 ? "" : "s"} drafted from your descriptions: approve below.` : "",
            result.resumed.length ? `${result.resumed.length} waiting job${result.resumed.length === 1 ? "" : "s"} continue now.` : "",
            ...warnings,
          ]
            .filter(Boolean)
            .join(" ") || undefined,
      });
      setDrafts({});
      if (result.suggestions.length) setSuggestions(result.suggestions);
      else onDone?.(result);
    } catch (error) {
      toast.error("Answers not saved", { description: describeError(error) });
    } finally {
      setBusy(false);
    }
  };

  if (suggestions) {
    return (
      <SuggestionsReview
        candidateId={candidateId}
        records={suggestions}
        onDone={() => {
          setSuggestions(null);
          refresh();
          onDone?.(null);
        }}
      />
    );
  }
  if (!questions.length) return <p className="text-sm text-muted-foreground">Nothing to answer: every skill this job asks for is answered or shown in the profile.</p>;

  return (
    <div className="space-y-3">
      <ul className="divide-y rounded-lg border" aria-label="Missing job skills">
        {questions.map((q) => {
          const d = draft(q.normalized);
          return (
            <li key={q.normalized} className="space-y-2 px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{q.skill}</span>
                <Badge variant={q.kind === "required" || (q.required_count ?? 0) > 0 ? "warning" : "outline"}>{demand(q)}</Badge>
                {q.answer === "unsure" && <Badge variant="outline">Answered “Unsure” before</Badge>}
                <div className="ml-auto flex gap-1" role="group" aria-label={`Has ${candidateName} used ${q.skill}?`}>
                  {DECISIONS.map(({ value, label, icon: Icon }) => (
                    <Button
                      key={value}
                      type="button"
                      size="sm"
                      variant={d.decision === value ? "default" : "outline"}
                      aria-pressed={d.decision === value}
                      aria-label={`${label}: ${q.skill}`}
                      onClick={() => set(q.normalized, { decision: d.decision === value ? null : value })}
                    >
                      <Icon /> {label}
                    </Button>
                  ))}
                </div>
              </div>
              {q.evidence && q.status === "plausible" && <p className="text-xs text-muted-foreground">Related in the profile: “{q.evidence}”</p>}
              {d.decision === "used" && (
                <div className="space-y-2 rounded-md bg-muted/40 p-3">
                  <div className="text-xs font-medium text-muted-foreground">
                    Where was it used? Ticked employers list it on their “Technologies” line; leave all unticked to list it under Skills only.
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-1.5">
                    {roles.map((role) => (
                      <label key={role.id} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={d.role_ids.includes(role.id)}
                          onCheckedChange={(v) => set(q.normalized, { role_ids: v === true ? [...d.role_ids, role.id] : d.role_ids.filter((r) => r !== role.id) })}
                          aria-label={`${q.skill} at ${role.label}`}
                        />
                        {role.label}
                      </label>
                    ))}
                  </div>
                  {d.role_ids.length > 0 && (
                    <div className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr]">
                      <Input
                        value={d.context}
                        onChange={(e) => set(q.normalized, { context: e.target.value })}
                        placeholder={`Optional: what did ${candidateName.split(" ")[0]} do with ${q.skill} there? (adds a bullet)`}
                        aria-label={`What was done with ${q.skill}`}
                      />
                      <Input value={d.project} onChange={(e) => set(q.normalized, { project: e.target.value })} placeholder="Project (optional)" aria-label={`Project for ${q.skill}`} />
                      <Input
                        value={d.outcome}
                        onChange={(e) => set(q.normalized, { outcome: e.target.value })}
                        placeholder="Result or scale (optional, real numbers only)"
                        aria-label={`Result for ${q.skill}`}
                      />
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {missingContext.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {missingContext.map((q) => q.skill).join(", ")} will appear on the ticked employers' Technologies line. Add a few words about the work if you also want a bullet.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">
          {answered.length} of {questions.length} answered
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ml-auto"
          disabled={busy || answered.length === questions.length}
          onClick={() => setDrafts((all) => Object.fromEntries(questions.map((q) => [q.normalized, all[q.normalized]?.decision ? all[q.normalized] : { ...EMPTY, decision: "unsure" as const }])))}
        >
          Mark the rest Unsure
        </Button>
        <Button type="button" size="sm" disabled={busy || answered.length === 0} onClick={() => void submit()}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Send />} Save {answered.length || ""} answer{answered.length === 1 ? "" : "s"}
        </Button>
      </div>
    </div>
  );
}
