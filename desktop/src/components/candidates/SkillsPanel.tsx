import { CheckCheck, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { useCandidateMutation, useVerificationRequired } from "@/lib/queries";
import type { CandidateDetail, CandidateSkill, VerificationStatus } from "@/lib/types";
import { formatRelativeTime } from "@/lib/utils";

type Filter = "all" | VerificationStatus;

export function SkillsPanel({ candidate }: { candidate: CandidateDetail }) {
  const id = candidate.id;
  const required = useVerificationRequired();
  const [filter, setFilter] = useState<Filter>("all");
  const [list, setList] = useState("");
  const [name, setName] = useState("");
  const [evidence, setEvidence] = useState("");
  const onError = (error: unknown) => toast.error("Couldn't update skill", { description: describeError(error) });

  const updateSkill = useCandidateMutation(id, (v: { skillId: string; body: Parameters<typeof api.candidates.updateSkill>[2] }) =>
    api.candidates.updateSkill(id, v.skillId, v.body),
  );
  const deleteSkill = useCandidateMutation(id, (skillId: string) => api.candidates.deleteSkill(id, skillId));
  const addSkill = useCandidateMutation(id, () =>
    api.candidates.addSkill(id, { skill_name: name, evidence: evidence || null, verification_status: evidence ? "verified" : "unverified" }),
  );
  const verifyAll = useCandidateMutation(id, () => api.candidates.verifySkillsWithEvidence(id).then((r) => r.candidate));
  const [lastAdded, setLastAdded] = useState<{ added: string[]; already: string[] } | null>(null);
  const addList = useCandidateMutation(id, () =>
    api.candidates.addSkills(id, list).then((r) => {
      setLastAdded({ added: r.added, already: r.already });
      return r.candidate;
    }),
  );

  const counts = { all: candidate.skills.length, unverified: 0, verified: 0, rejected: 0 } as Record<Filter, number>;
  for (const s of candidate.skills) counts[s.verification_status] += 1;
  const shown = candidate.skills.filter((s) => !required || filter === "all" || s.verification_status === filter);

  return (
    <div className="space-y-3">
      {!required && (
        <p className="text-sm text-muted-foreground">
          Every skill marked <span className="font-medium text-foreground">Use</span> is available to tailored resumes; each resume lists the ones the job asks for first (up to 24).
        </p>
      )}
      {required && (
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<Filter>
            ariaLabel="Skill filter"
            value={filter}
            onValueChange={setFilter}
            options={[
              { value: "all", label: `All ${counts.all}` },
              { value: "unverified", label: `To review ${counts.unverified}` },
              { value: "verified", label: `Verified ${counts.verified}` },
              { value: "rejected", label: `Rejected ${counts.rejected}` },
            ]}
          />
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            disabled={verifyAll.isPending || !candidate.skills.some((s) => s.verification_status === "unverified" && s.evidence)}
            onClick={() =>
              verifyAll.mutate(undefined, {
                onSuccess: () => toast.success("Skills with evidence verified"),
                onError,
              })
            }
          >
            <CheckCheck /> Verify all that have evidence
          </Button>
        </div>
      )}

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[28%]">Skill</TableHead>
              <TableHead>{required ? "Evidence" : "Where you used it (optional)"}</TableHead>
              <TableHead className={required ? "w-[250px]" : "w-[180px]"}>{required ? "Status" : "On resumes"}</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.length === 0 && (
              <TableRow>
                <TableCell colSpan={4} className="py-8 text-center text-sm text-muted-foreground">
                  No skills in this view.
                </TableCell>
              </TableRow>
            )}
            {shown.map((skill) => (
              <SkillRow
                key={`${skill.id}-${skill.updated_at}`}
                skill={skill}
                required={required}
                onStatus={(status) => updateSkill.mutate({ skillId: skill.id, body: { verification_status: status } }, { onError })}
                onEvidence={(value) => updateSkill.mutate({ skillId: skill.id, body: { evidence: value } }, { onError })}
                onDelete={() => deleteSkill.mutate(skill.id, { onError })}
              />
            ))}
          </TableBody>
        </Table>
      </div>

      {!required && (
        <form
          className="space-y-2 rounded-lg border bg-muted/30 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!list.trim()) return;
            addList.mutate(undefined, {
              onSuccess: () => setList(""),
              onError,
            });
          }}
        >
          <label className="block text-xs font-medium text-muted-foreground" htmlFor="skill-list">
            Add skills — paste a list separated by commas or new lines
          </label>
          <Textarea id="skill-list" rows={3} value={list} onChange={(e) => setList(e.target.value)} placeholder="e.g. Kubernetes, Terraform, Azure DevOps, GraphQL, Redis" />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" disabled={!list.trim() || addList.isPending}>
              <Plus /> Add skills
            </Button>
            {lastAdded && (
              <span className="text-xs text-muted-foreground" role="status">
                {lastAdded.added.length} added{lastAdded.already.length > 0 && ` · already listed: ${lastAdded.already.join(", ")}`}
              </span>
            )}
          </div>
        </form>
      )}
      {required && (
        <form
          className="flex flex-wrap items-end gap-2 rounded-lg border bg-muted/30 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!name.trim()) return;
            addSkill.mutate(undefined, {
              onSuccess: () => {
                setName("");
                setEvidence("");
                toast.success("Skill added");
              },
              onError,
            });
          }}
        >
          <div className="w-48">
            <label className="mb-1 block text-xs font-medium text-muted-foreground" htmlFor="new-skill">
              Add a skill
            </label>
            <Input id="new-skill" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Kubernetes" />
          </div>
          <div className="min-w-60 flex-1">
            <label className="mb-1 block text-xs font-medium text-muted-foreground" htmlFor="new-evidence">
              Evidence (adding evidence marks it verified)
            </label>
            <Input id="new-evidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="Where it appears in the resume or how you confirmed it" />
          </div>
          <Button type="submit" disabled={!name.trim() || addSkill.isPending}>
            <Plus /> Add
          </Button>
        </form>
      )}
    </div>
  );
}

function SkillRow({
  skill,
  required,
  onStatus,
  onEvidence,
  onDelete,
}: {
  skill: CandidateSkill;
  required: boolean;
  onStatus: (status: VerificationStatus) => void;
  onEvidence: (value: string) => void;
  onDelete: () => void;
}) {
  const [evidence, setEvidence] = useState(skill.evidence ?? "");
  return (
    <TableRow>
      <TableCell className="font-medium">
        <div className="flex flex-wrap items-center gap-1.5">
          {skill.skill_name}
          {skill.decision === "used" && (
            <Badge variant="success" title={skill.decided_at ? `Confirmed ${formatRelativeTime(skill.decided_at)}` : undefined}>
              Confirmed{skill.role_ids?.length ? ` · ${skill.role_ids.length} role${skill.role_ids.length === 1 ? "" : "s"}` : " · general"}
            </Badge>
          )}
          {skill.decision === "unsure" && <Badge variant="warning">Unsure</Badge>}
          {skill.decision === "not_used" && <Badge variant="outline">Have not used</Badge>}
        </div>
      </TableCell>
      <TableCell>
        <Input
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          onBlur={() => evidence !== (skill.evidence ?? "") && onEvidence(evidence)}
          placeholder={required ? "Add evidence to allow verification" : "e.g. Order platform at Contoso"}
          className="h-8 text-xs"
          aria-label={`Evidence for ${skill.skill_name}`}
        />
      </TableCell>
      <TableCell>
        {required ? (
          <Segmented<VerificationStatus>
            ariaLabel={`Status of ${skill.skill_name}`}
            value={skill.verification_status}
            onValueChange={onStatus}
            options={[
              { value: "unverified", label: "Review" },
              { value: "verified", label: "Verified" },
              { value: "rejected", label: "Reject" },
            ]}
          />
        ) : (
          <Segmented<"use" | "rejected">
            ariaLabel={`Use ${skill.skill_name} on resumes`}
            value={skill.verification_status === "rejected" ? "rejected" : "use"}
            onValueChange={(value) => onStatus(value === "use" ? "unverified" : "rejected")}
            options={[
              { value: "use", label: "Use" },
              { value: "rejected", label: "Don't use" },
            ]}
          />
        )}
      </TableCell>
      <TableCell>
        <Button variant="ghost" size="icon-sm" className="text-muted-foreground hover:text-destructive" onClick={onDelete} aria-label={`Delete ${skill.skill_name}`}>
          <Trash2 />
        </Button>
      </TableCell>
    </TableRow>
  );
}
