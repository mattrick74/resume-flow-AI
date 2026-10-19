import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, LoaderCircle, ScanText, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { api } from "@/lib/api";
import { queryKeys, useSettings, useVerificationRequired } from "@/lib/queries";
import type { CandidateDetail, CandidateListItem, ReextractResult, ResumeVersionInfo } from "@/lib/types";

/** True when an OpenAI key is stored, so re-reading uses AI extraction. */
export function useAiReading() {
  const settings = useSettings();
  return { ai: Boolean(settings.data?.secrets_configured.openai_api_key), model: settings.data?.settings.analysis_model };
}

export function summarize(result: ReextractResult, required = true) {
  const found = `${result.roles} role${result.roles === 1 ? "" : "s"}, ${result.bullets} bullet${result.bullets === 1 ? "" : "s"}, ${result.education} education`;
  const removed = result.skills_removed ? `; ${result.skills_removed} skill${result.skills_removed === 1 ? "" : "s"} from the earlier reading were removed` : "";
  const warnings = result.warnings.length ? ` ${result.warnings.join(" ")}` : "";
  return {
    title: `${result.extraction_source === "ai" ? "Read with AI" : "Read offline"}: ${found}`,
    description: required
      ? `${result.kept_verified} verified fact${result.kept_verified === 1 ? "" : "s"} kept their tick${removed}. Review the rest, then mark the review complete.${warnings}`
      : `The profile now uses this reading${removed}.${warnings}`,
  };
}

function Explanation({ ai, model }: { ai: boolean; model?: string }) {
  const required = useVerificationRequired();
  return (
    <div className="space-y-2 text-sm text-muted-foreground">
      <p>
        {ai
          ? `The stored resume is read again with AI (${model ?? "your analysis model"}), which handles any layout. It costs well under a cent per resume.`
          : "The stored resume is read again with the offline parser. Add an OpenAI key in Settings for AI reading, which handles any layout."}
      </p>
      {required ? (
        <p>
          The extracted profile is replaced. Facts you already verified keep their tick when they come back word for word; new or changed facts need your
          review, and you'll mark the review complete again before the candidate is matched. Skills read from this resume before are replaced by this reading;
          skills you added yourself are kept.
        </p>
      ) : (
        <p>
          Experience, education, certifications and summary are replaced by this reading, including roles or bullets you edited by hand. Skills read from this
          resume before are replaced too; skills you added yourself are kept.
        </p>
      )}
    </div>
  );
}

/** "Re-read" for one stored resume version. */
export function ReextractButton({ candidate, version }: { candidate: CandidateDetail; version: ResumeVersionInfo }) {
  const client = useQueryClient();
  const { ai, model } = useAiReading();
  const required = useVerificationRequired();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    try {
      const result = await api.candidates.reextract(candidate.id, version.id);
      client.setQueryData(queryKeys.candidate(candidate.id), result.candidate);
      void client.invalidateQueries({ queryKey: queryKeys.candidates });
      const { title, description } = summarize(result, required);
      toast.success(title, { description });
      setOpen(false);
    } catch (error) {
      toast.error("Couldn't read the resume again", { description: describeError(error) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
        {ai ? <Sparkles /> : <ScanText />} Re-read
      </Button>
      <Modal
        open={open}
        onOpenChange={(value) => !busy && setOpen(value)}
        title={`Re-read “${version.original_filename}”?`}
        footer={
          <>
            <Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button disabled={busy} onClick={() => void run()}>
              {busy ? <LoaderCircle className="animate-spin" /> : ai ? <Sparkles /> : <ScanText />} {busy ? "Reading…" : "Re-read resume"}
            </Button>
          </>
        }
      >
        <Explanation ai={ai} model={model} />
      </Modal>
    </>
  );
}

type Row = { id: string; name: string; state: "waiting" | "reading" | "done" | "failed" | "skipped"; note?: string };

/** Re-reads every listed candidate's resume, one at a time. */
export function ReextractAllDialog({ candidates, open, onOpenChange }: { candidates: CandidateListItem[]; open: boolean; onOpenChange: (open: boolean) => void }) {
  const client = useQueryClient();
  const { ai, model } = useAiReading();
  const [rows, setRows] = useState<Row[] | null>(null);
  const running = rows?.some((r) => r.state === "reading" || r.state === "waiting") ?? false;

  const update = (id: string, changes: Partial<Row>) => setRows((list) => list?.map((r) => (r.id === id ? { ...r, ...changes } : r)) ?? null);

  const start = async () => {
    const queue = candidates.filter((c) => c.latest_version !== null);
    setRows(queue.map((c) => ({ id: c.id, name: c.full_name, state: "waiting" })));
    for (const item of queue) {
      update(item.id, { state: "reading" });
      try {
        const detail = await api.candidates.get(item.id);
        const version = detail.resume_versions.find((v) => v.id === detail.profile.source_version_id) ?? detail.resume_versions[0];
        if (!version) {
          update(item.id, { state: "skipped", note: "No stored resume" });
          continue;
        }
        const result = await api.candidates.reextract(item.id, version.id);
        client.setQueryData(queryKeys.candidate(item.id), result.candidate);
        update(item.id, { state: "done", note: summarize(result).title });
      } catch (error) {
        update(item.id, { state: "failed", note: describeError(error) });
      }
    }
    void client.invalidateQueries({ queryKey: queryKeys.candidates });
  };

  const close = (value: boolean) => {
    if (running) return;
    onOpenChange(value);
    if (!value) setRows(null);
  };

  return (
    <Modal
      open={open}
      onOpenChange={close}
      title="Re-read all resumes"
      description={`${candidates.length} candidate${candidates.length === 1 ? "" : "s"} in the current list.`}
      className="max-w-xl"
      footer={
        rows ? (
          <Button disabled={running} onClick={() => close(false)}>
            {running ? <LoaderCircle className="animate-spin" /> : null} {running ? "Reading…" : "Done"}
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => close(false)}>
              Cancel
            </Button>
            <Button disabled={!candidates.length} onClick={() => void start()}>
              {ai ? <Sparkles /> : <ScanText />} Re-read {candidates.length} resume{candidates.length === 1 ? "" : "s"}
            </Button>
          </>
        )
      }
    >
      {rows ? (
        <ul className="divide-y rounded-lg border" aria-label="Re-read progress">
          {rows.map((row) => (
            <li key={row.id} className="flex items-start gap-2 px-3 py-2 text-sm">
              {row.state === "reading" ? (
                <LoaderCircle className="mt-0.5 size-4 shrink-0 animate-spin text-primary" />
              ) : row.state === "done" ? (
                <CircleCheck className="mt-0.5 size-4 shrink-0 text-success" />
              ) : row.state === "failed" || row.state === "skipped" ? (
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
              ) : (
                <span className="mt-0.5 size-4 shrink-0 rounded-full border" />
              )}
              <div className="min-w-0">
                <div className="font-medium">{row.name}</div>
                {row.note && <div className="text-xs text-muted-foreground">{row.note}</div>}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <Explanation ai={ai} model={model} />
      )}
    </Modal>
  );
}
