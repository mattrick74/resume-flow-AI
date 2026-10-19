import { useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, CircleAlert, LoaderCircle, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { ExperienceRecord, FollowUp } from "@/lib/types";

const DRAFTED_BY = { ai: "Drafted by AI from your description", offline: "Your description, tidied", operator: "Written by you" } as const;

/**
 * Drafted bullets wait here (v1.8 VAL-09, GEN-11): edit, approve or reject each one. Only approved bullets can appear
 * in documents. With ``applicationId`` approving also regenerates that application with the additions.
 */
export function SuggestionsReview({
  candidateId,
  records,
  applicationId,
  onDone,
}: {
  candidateId: string;
  records: ExperienceRecord[];
  applicationId?: string;
  onDone?: (result: FollowUp | null) => void;
}) {
  const client = useQueryClient();
  const [texts, setTexts] = useState<Record<string, string>>(() => Object.fromEntries(records.map((r) => [r.id, r.narrative ?? ""])));
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(records.map((r) => r.id)));
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  const [blocked, setBlocked] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const visible = records.filter((r) => !rejected.has(r.id));
  const ids = visible.filter((r) => chosen.has(r.id)).map((r) => r.id);

  const refresh = () => {
    for (const key of [queryKeys.candidate(candidateId), ["evidence", candidateId], queryKeys.jobs, queryKeys.dashboard, ["skill-gaps"], ["batch-gaps"]]) {
      void client.invalidateQueries({ queryKey: key });
    }
    if (applicationId) {
      void client.invalidateQueries({ queryKey: queryKeys.application(applicationId) });
      void client.invalidateQueries({ queryKey: ["coverage", applicationId] });
    }
  };

  const approve = async (acknowledge: boolean) => {
    setBusy(true);
    const edits = Object.fromEntries(ids.map((id) => [id, texts[id] ?? ""]).filter(([id, text]) => text.trim() && text !== records.find((r) => r.id === id)?.narrative));
    try {
      const result = applicationId
        ? await api.applications.approveAdditions(applicationId, { record_ids: ids, edits, acknowledge, regenerate: true })
        : await api.candidates.approveEvidence(candidateId, ids, { acknowledge, edits });
      refresh();
      setBlocked(null);
      toast.success(`${ids.length} bullet${ids.length === 1 ? "" : "s"} approved`, {
        description:
          [
            applicationId ? "Regenerating this application with them." : "They're used in every tailored resume from now on.",
            result.resumed.length ? `${result.resumed.length} waiting job${result.resumed.length === 1 ? "" : "s"} continue now.` : "",
          ]
            .filter(Boolean)
            .join(" "),
      });
      onDone?.(result);
    } catch (error) {
      if (error instanceof ApiError && error.code === "unresolved_issues") setBlocked(error.message);
      else toast.error("Not approved", { description: describeError(error) });
    } finally {
      setBusy(false);
    }
  };

  const reject = async (record: ExperienceRecord) => {
    try {
      await api.candidates.updateEvidence(candidateId, record.id, { status: "rejected" });
      setRejected((all) => new Set(all).add(record.id));
      refresh();
    } catch (error) {
      toast.error("Not rejected", { description: describeError(error) });
    }
  };

  if (!visible.length) {
    return (
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        No drafted bullets left to review.
        {onDone && (
          <Button size="sm" variant="ghost" onClick={() => onDone(null)}>
            Close
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Each bullet is written only from what you said about the work. Check it reads true, edit if needed, then approve. Nothing appears in a resume until it's approved.
      </p>
      <ul className="space-y-3" aria-label="Suggested bullets">
        {visible.map((record) => (
          <li key={record.id} className="rounded-lg border p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Checkbox
                checked={chosen.has(record.id)}
                onCheckedChange={(v) => setChosen((all) => { const next = new Set(all); if (v === true) next.add(record.id); else next.delete(record.id); return next; })}
                aria-label={`Approve the bullet for ${record.role}`}
              />
              <span className="text-sm font-medium">{record.role}</span>
              {record.skills.map((s) => (
                <Badge key={s} variant="secondary">
                  {s}
                </Badge>
              ))}
              <Button size="icon-sm" variant="ghost" className="ml-auto text-muted-foreground hover:text-destructive" onClick={() => void reject(record)} aria-label={`Reject the bullet for ${record.role}`}>
                <X />
              </Button>
            </div>
            <Textarea
              rows={2}
              value={texts[record.id] ?? ""}
              onChange={(e) => setTexts((all) => ({ ...all, [record.id]: e.target.value }))}
              aria-label={`Bullet for ${record.role}`}
            />
            <div className="mt-1.5 text-xs text-muted-foreground">
              {record.drafted_by ? DRAFTED_BY[record.drafted_by] : "Draft"} · you said: “{record.context}”
              {record.outcome && ` · result: “${record.outcome}”`}
            </div>
            {record.issues.length > 0 && (
              <ul className="mt-2 space-y-1">
                {record.issues.map((issue) => (
                  <li key={issue.code + issue.message} className={issue.severity === "error" ? "flex gap-1.5 text-xs text-destructive" : "flex gap-1.5 text-xs text-warning"}>
                    {issue.severity === "error" ? <CircleAlert className="size-3.5 shrink-0" aria-hidden /> : <TriangleAlert className="size-3.5 shrink-0" aria-hidden />}
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      {blocked && (
        <Alert
          variant="warning"
          title="Some bullets say more than your description"
          action={
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void approve(true)}>
              Approve anyway
            </Button>
          }
        >
          {blocked} Edit them so they only say what happened, or approve anyway if you've checked they're true.
        </Alert>
      )}
      <div className="flex justify-end">
        <Button size="sm" disabled={busy || ids.length === 0} onClick={() => void approve(false)}>
          {busy ? <LoaderCircle className="animate-spin" /> : <BadgeCheck />} Approve {ids.length} bullet{ids.length === 1 ? "" : "s"}
          {applicationId ? " and regenerate" : ""}
        </Button>
      </div>
    </div>
  );
}
