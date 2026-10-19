import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle, Pencil, Plus, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { SuggestionsReview } from "@/components/experience/SuggestionsReview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { queryKeys, useSettings } from "@/lib/queries";
import type { CandidateDetail, ExperienceRecord, SkillDecision } from "@/lib/types";
import { formatRelativeTime } from "@/lib/utils";

const DECISION_LABEL: Record<SkillDecision, string> = { used: "Have used", not_used: "Have not used", unsure: "Unsure" };

/** UX-02: suggested bullets, approved additions, the experience interview and the confirmation history. */
export function EvidencePanel({ candidate }: { candidate: CandidateDetail }) {
  const client = useQueryClient();
  const id = candidate.id;
  const evidence = useQuery({ queryKey: ["evidence", id], queryFn: () => api.candidates.evidence(id) });
  const refresh = () => {
    void evidence.refetch();
    void client.invalidateQueries({ queryKey: queryKeys.candidate(id) });
    void client.invalidateQueries({ queryKey: queryKeys.jobs });
  };
  if (evidence.isPending) return <Skeleton className="h-64" />;
  if (evidence.isError) return <p className="text-sm text-muted-foreground">Couldn't load the evidence for this candidate.</p>;
  const { records, history, roles } = evidence.data;
  const suggested = records.filter((r) => r.status === "suggested");
  const approved = records.filter((r) => r.status === "approved");
  const rejected = records.filter((r) => r.status === "rejected");
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Experience you confirmed beyond the imported resume. The original resume file is never changed; approved bullets are added to their role in every tailored resume, and each one keeps a record of when and why it was added.
      </p>
      {suggested.length > 0 && (
        <Card className="space-y-3 p-4">
          <h3 className="text-sm font-semibold">Suggested bullets ({suggested.length})</h3>
          <SuggestionsReview key={suggested.map((r) => r.id).join()} candidateId={id} records={suggested} onDone={refresh} />
        </Card>
      )}
      <InterviewForm candidateId={id} roles={roles} disabled={!candidate.consent_confirmed_at} onAdded={refresh} />
      <Card className="space-y-3 p-4">
        <h3 className="text-sm font-semibold">Approved additions ({approved.length})</h3>
        {approved.length === 0 ? (
          <p className="text-sm text-muted-foreground">None yet. Answer a job's missing skills, or add experience above.</p>
        ) : (
          <ul className="space-y-2">
            {approved.map((record) => (
              <ApprovedRecord key={`${record.id}-${record.updated_at}`} candidateId={id} record={record} onChanged={refresh} />
            ))}
          </ul>
        )}
        {rejected.length > 0 && (
          <details>
            <summary className="cursor-pointer text-xs font-medium text-muted-foreground">{rejected.length} rejected</summary>
            <ul className="mt-2 space-y-1.5 text-sm">
              {rejected.map((r) => (
                <li key={r.id} className="flex items-start gap-2">
                  <span className="flex-1 text-muted-foreground line-through">{r.narrative}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      api.candidates
                        .updateEvidence(id, r.id, { status: "suggested" })
                        .then(refresh)
                        .catch((e) => toast.error("Not restored", { description: describeError(e) }))
                    }
                  >
                    <Undo2 /> Restore
                  </Button>
                </li>
              ))}
            </ul>
          </details>
        )}
      </Card>
      <Card className="space-y-3 p-4">
        <h3 className="text-sm font-semibold">Confirmation history</h3>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No answers yet.</p>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Skill</TableHead>
                  <TableHead>Answer</TableHead>
                  <TableHead>Where</TableHead>
                  <TableHead>From jobs</TableHead>
                  <TableHead className="text-right">When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((h) => (
                  <TableRow key={h.normalized}>
                    <TableCell className="font-medium">{h.skill}</TableCell>
                    <TableCell>
                      <Badge variant={h.decision === "used" ? "success" : h.decision === "not_used" ? "outline" : "warning"}>{DECISION_LABEL[h.decision]}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{h.decision === "used" ? h.roles.join("; ") || "General (Skills only)" : "—"}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{h.jobs.map((j) => [j.title, j.company].filter(Boolean).join(" · ")).join("; ") || "—"}</TableCell>
                    <TableCell className="text-right text-xs text-muted-foreground">{formatRelativeTime(h.decided_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}

function ApprovedRecord({ candidateId, record, onChanged }: { candidateId: string; record: ExperienceRecord; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(record.narrative ?? "");
  const act = (promise: Promise<unknown>, message: string) =>
    promise.then(() => {
      toast.success(message);
      setEditing(false);
      onChanged();
    }, (e) => toast.error("Not saved", { description: describeError(e) }));
  return (
    <li className="rounded-lg border p-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{record.role}</span>
        {record.skills.map((s) => (
          <Badge key={s} variant="secondary">
            {s}
          </Badge>
        ))}
        {!record.role_found && (
          <Badge variant="destructive">
            <CircleAlert /> Role no longer in the profile
          </Badge>
        )}
        <span className="ml-auto text-xs text-muted-foreground">approved {record.approved_at ? formatRelativeTime(record.approved_at) : ""}</span>
      </div>
      {editing ? (
        <div className="space-y-2">
          <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label={`Edit the bullet for ${record.role}`} />
          <div className="flex gap-2">
            <Button size="sm" disabled={!text.trim()} onClick={() => void act(api.candidates.updateEvidence(candidateId, record.id, { narrative: text, status: "approved", acknowledge: false }), "Bullet updated")}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex items-start gap-2">
          <p className="flex-1 text-sm">{record.narrative}</p>
          <Button size="icon-sm" variant="ghost" onClick={() => setEditing(true)} aria-label={`Edit the bullet for ${record.role}`}>
            <Pencil />
          </Button>
          <Button size="icon-sm" variant="ghost" onClick={() => void act(api.candidates.updateEvidence(candidateId, record.id, { status: "suggested" }), "Approval undone")} aria-label={`Undo approval for ${record.role}`}>
            <Undo2 />
          </Button>
          <Button size="icon-sm" variant="ghost" className="hover:text-destructive" onClick={() => void act(api.candidates.deleteEvidence(candidateId, record.id), "Bullet deleted")} aria-label={`Delete the bullet for ${record.role}`}>
            <Trash2 />
          </Button>
        </div>
      )}
    </li>
  );
}

/** CAN-15: the experience interview — one piece of work at one role, in your own words; a bullet is drafted for approval. */
function InterviewForm({ candidateId, roles, disabled, onAdded }: { candidateId: string; roles: { id: string; label: string }[]; disabled: boolean; onAdded: () => void }) {
  const [roleId, setRoleId] = useState(roles[0]?.id ?? "");
  const [skills, setSkills] = useState("");
  const [context, setContext] = useState("");
  const [project, setProject] = useState("");
  const [outcome, setOutcome] = useState("");
  const [busy, setBusy] = useState(false);
  const instant = useSettings().data?.settings.auto_approve_bullets ?? true;
  if (!roles.length) return null;
  const submit = async () => {
    setBusy(true);
    try {
      await api.candidates.addEvidence(candidateId, {
        role_id: roleId,
        skills: skills.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean),
        context: context.trim(),
        project: project.trim() || null,
        outcome: outcome.trim() || null,
        origin: "interview",
      });
      toast.success(instant ? "Bullet added" : "Bullet drafted", {
        description: instant ? "It's used in every tailored resume from now on." : "Review it under Suggested bullets, then approve.",
      });
      setSkills("");
      setContext("");
      setProject("");
      setOutcome("");
      onAdded();
    } catch (error) {
      toast.error("Not added", { description: describeError(error) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="space-y-3 p-4">
      <div>
        <h3 className="text-sm font-semibold">Add experience</h3>
        <p className="text-xs text-muted-foreground">Work the resume left out: which employer, what was built or improved, and with which technologies. A bullet is written from exactly this.</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="interview-role">Employer and role</Label>
          <NativeSelect id="interview-role" value={roleId} onChange={(e) => setRoleId(e.target.value)}>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="interview-skills">Technologies used there</Label>
          <Input id="interview-skills" value={skills} onChange={(e) => setSkills(e.target.value)} placeholder="e.g. Kafka, .NET 8" />
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="interview-context">What did they build or improve?</Label>
          <Input id="interview-context" value={context} onChange={(e) => setContext(e.target.value)} placeholder="e.g. built order-event processing between the web shop and the warehouse system" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="interview-project">Project (optional)</Label>
          <Input id="interview-project" value={project} onChange={(e) => setProject(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="interview-outcome">Result or scale (optional, real numbers only)</Label>
          <Input id="interview-outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} placeholder="e.g. about 2M messages a day" />
        </div>
      </div>
      <div className="flex justify-end">
        <Button size="sm" disabled={disabled || busy || context.trim().length < 3} onClick={() => void submit()} title={disabled ? "Confirm the candidate's authorization first" : undefined}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Plus />} {instant ? "Add bullet" : "Draft a bullet"}
        </Button>
      </div>
    </Card>
  );
}
