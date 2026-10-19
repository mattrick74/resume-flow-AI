import { useQuery } from "@tanstack/react-query";

import { SkillQuestions } from "@/components/experience/SkillQuestions";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";

/**
 * UX-04 / JOB-11: one session for every job waiting for answers. Missing skills are listed per candidate, the ones
 * most jobs require first; one answer applies to every job that asked, and those jobs continue automatically.
 */
export function BatchConfirmDialog({ open, onOpenChange, jobIds }: { open: boolean; onOpenChange: (open: boolean) => void; jobIds?: string[] }) {
  const gaps = useQuery({ queryKey: ["batch-gaps", jobIds ?? "waiting"], queryFn: () => api.jobs.batchGaps(jobIds ?? []), enabled: open });
  const data = gaps.data;
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Answer missing job skills"
      description={data ? `${data.jobs} job${data.jobs === 1 ? "" : "s"} asked for skills their top candidate's profile doesn't show. Answer once; every job that asked uses it.` : undefined}
      className="max-w-4xl"
      footer={
        <Button variant="ghost" onClick={() => onOpenChange(false)}>
          Close
        </Button>
      }
    >
      <div className="max-h-[65vh] space-y-6 overflow-y-auto pr-1">
        {gaps.isPending && <Skeleton className="h-40" />}
        {data && data.candidates.length === 0 && <p className="text-sm text-muted-foreground">No job is waiting for answers.</p>}
        {data?.candidates.map((bucket) => {
          const open = bucket.skills.filter((s) => s.status === "plausible" || s.status === "unknown");
          return (
            <section key={bucket.candidate_id} className="space-y-2">
              <h3 className="text-sm font-semibold">
                {bucket.candidate_name} <span className="font-normal text-muted-foreground">· {bucket.job_ids.length} job{bucket.job_ids.length === 1 ? "" : "s"}</span>
              </h3>
              <SkillQuestions
                candidateId={bucket.candidate_id}
                candidateName={bucket.candidate_name}
                roles={bucket.roles}
                questions={open.map((s) => ({
                  skill: s.skill,
                  normalized: s.normalized,
                  status: s.status,
                  evidence: s.evidence,
                  answer: s.answer,
                  required_count: s.required_count,
                  preferred_count: s.preferred_count,
                }))}
                sourceJobIds={bucket.job_ids}
                onDone={() => void gaps.refetch()}
              />
            </section>
          );
        })}
      </div>
    </Modal>
  );
}
