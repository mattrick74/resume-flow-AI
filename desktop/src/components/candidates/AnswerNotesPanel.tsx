import { useEffect, useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { useCandidateMutation } from "@/lib/queries";
import type { CandidateDetail } from "@/lib/types";

const LIMIT = 20_000;

const PLACEHOLDER = `For example:
Salary: 145-160k base, flexible for remote roles.
Open to relocating to Austin or Denver; no relocation abroad.
Available two weeks after an offer.
US citizen, no sponsorship needed.
Kubernetes: ran a 3-node home lab since 2021 (not on the resume).
Why data platforms: I like making other teams faster...`;

/**
 * v2.0: what the resume doesn't say but application forms ask: salary, relocation, availability, eligibility, the
 * story behind a project. Only question answering in the Chrome extension reads it; resumes and cover letters never do.
 */
export function AnswerNotesPanel({ candidate }: { candidate: CandidateDetail }) {
  const saved = candidate.answer_notes ?? "";
  const [text, setText] = useState(saved);
  useEffect(() => setText(saved), [saved]);
  const save = useCandidateMutation(candidate.id, (answer_notes: string) => api.candidates.update(candidate.id, { answer_notes }));
  const dirty = text.trim() !== saved;

  return (
    <Card className="space-y-3 p-5">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Notes for application questions</h3>
        <p className="text-sm text-muted-foreground">
          Write anything an application form might ask that the resume doesn&apos;t show. When the extension answers a form, the AI reads these
          notes with the profile and uses them as {candidate.full_name}&apos;s own statements. They are never added to resumes or cover letters.
        </p>
      </div>
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={14}
        maxLength={LIMIT}
        placeholder={PLACEHOLDER}
        aria-label="Notes for application questions"
      />
      <div className="flex items-center gap-2">
        <span className="flex-1 text-xs text-muted-foreground">
          {text.length.toLocaleString()} / {LIMIT.toLocaleString()} characters
        </span>
        <Button variant="ghost" size="sm" disabled={!dirty || save.isPending} onClick={() => setText(saved)}>
          Discard
        </Button>
        <Button
          size="sm"
          disabled={!dirty || save.isPending}
          onClick={() =>
            save.mutate(text, {
              onSuccess: () => toast.success("Notes saved"),
              onError: (error) => toast.error("Notes not saved", { description: describeError(error) }),
            })
          }
        >
          Save notes
        </Button>
      </div>
    </Card>
  );
}
