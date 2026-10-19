import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { SkillQuestions } from "@/components/experience/SkillQuestions";
import { SuggestionsReview } from "@/components/experience/SuggestionsReview";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { ApplicationDetail } from "@/lib/types";

/** Regenerates both documents from the current profile, including newly approved additions (GEN-10). */
export function useRegenerateWithAdditions(app: ApplicationDetail) {
  const client = useQueryClient();
  return async () => {
    try {
      const result = await api.applications.approveAdditions(app.id, { record_ids: [], regenerate: true });
      client.setQueryData(queryKeys.application(app.id), result.application);
      void client.invalidateQueries({ queryKey: ["coverage", app.id] });
      toast.success("Regenerating with the approved additions");
    } catch (error) {
      toast.error("Couldn't regenerate", { description: describeError(error) });
    }
  };
}

/**
 * GEN-11: an internal panel, separate from the documents. Job skills to answer and drafted bullets to approve; nothing
 * here reaches a document until it's confirmed and approved.
 */
export function ImprovePanel({ app }: { app: ApplicationDetail }) {
  const data = useQuery({ queryKey: ["suggestions", app.id], queryFn: () => api.applications.suggestAdditions(app.id) });
  const regenerate = useRegenerateWithAdditions(app);
  if (data.isPending) return <Skeleton className="h-48" />;
  if (data.isError) return <p className="text-sm text-muted-foreground">Suggestions aren't available for this application.</p>;
  const { gaps, suggestions } = data.data;
  const open = gaps.items.filter((i) => i.status === "plausible" || i.status === "unknown");
  const newer = (data.data.current_profile_revision ?? 0) > (data.data.profile_revision ?? 0);
  const busy = app.status === "GENERATING" || app.status === "VALIDATING";
  return (
    <div className="space-y-4">
      {newer && (
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <div className="flex-1 text-sm">
            {gaps.candidate_name}'s profile has newer confirmed experience than these documents.
          </div>
          <Button size="sm" disabled={busy} onClick={() => void regenerate()}>
            <RefreshCw /> Regenerate with approved additions
          </Button>
        </Card>
      )}
      <Card className="space-y-3 p-4">
        <h3 className="text-sm font-semibold">Job skills to answer</h3>
        <p className="text-xs text-muted-foreground">
          Skills this job asks for that {gaps.candidate_name.split(" ")[0]}'s profile doesn't show. Answer only what is true: confirmed skills and bullets from your descriptions are added
          and these documents are regenerated with them straight away.
        </p>
        <SkillQuestions
          candidateId={gaps.candidate_id}
          candidateName={gaps.candidate_name}
          roles={gaps.roles}
          questions={open.map((i) => ({ skill: i.skill, normalized: i.normalized, status: i.status, evidence: i.evidence, answer: i.answer, kind: i.kind }))}
          sourceJobIds={[app.job_id]}
          onDone={(result) => {
            void data.refetch();
            // Anything confirmed or added goes straight into this application's documents.
            const changed = (result?.approved?.length ?? 0) > 0 || result?.answers.some((a) => a.decision === "used");
            if (changed && !busy) void regenerate();
          }}
        />
      </Card>
      {suggestions.length > 0 && (
        <Card className="space-y-3 p-4">
          <h3 className="text-sm font-semibold">Drafted bullets awaiting approval</h3>
          <SuggestionsReview candidateId={gaps.candidate_id} records={suggestions} applicationId={app.id} onDone={() => void data.refetch()} />
        </Card>
      )}
    </div>
  );
}
