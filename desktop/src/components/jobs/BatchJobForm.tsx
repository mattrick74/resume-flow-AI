import { useQueryClient } from "@tanstack/react-query";
import { FileUp, LoaderCircle, Plus, Save, Sparkles, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { BatchItemResult, DuplicateMatch } from "@/lib/types";
import { cn, newId } from "@/lib/utils";

interface Entry {
  key: string;
  job_url: string;
  raw_jd: string;
  company: string;
  title: string;
  problem?: { kind: "duplicate" | "invalid"; message: string };
}

const blank = (): Entry => ({ key: newId(), job_url: "", raw_jd: "", company: "", title: "" });
const isBlank = (e: Entry) => !e.job_url.trim() && !e.raw_jd.trim() && !e.company.trim() && !e.title.trim();

function validUrl(value: string) {
  try {
    const url = new URL(value.trim());
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

function localProblem(entry: Entry): string | null {
  if (!validUrl(entry.job_url)) return "Enter the full posting URL starting with https://";
  if (entry.raw_jd.trim().length < 50) return "Paste the full job description";
  return null;
}

/** "URL: https://…" or a bare URL on one of the first lines becomes the job URL; the rest is the description. */
export function parseJobText(text: string): Pick<Entry, "job_url" | "raw_jd"> {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  for (let i = 0; i < Math.min(lines.length, 5); i++) {
    const match = /^\s*(?:url\s*:\s*)?(https?:\/\/\S+)\s*$/i.exec(lines[i]);
    if (match) {
      const rest = [...lines.slice(0, i), ...lines.slice(i + 1)].join("\n").trim();
      return { job_url: match[1], raw_jd: rest };
    }
  }
  return { job_url: "", raw_jd: text.trim() };
}

function duplicateMessage(result: BatchItemResult) {
  const match = (result.details as { matches?: DuplicateMatch[] } | undefined)?.matches?.[0];
  if (!match) return result.message ?? "Looks like a job you already saved";
  const what = [match.title, match.company].filter(Boolean).join(" · ") || "an earlier job";
  const why = match.reason === "same_url" ? "same URL" : match.reason === "same_text" ? "identical description" : `${Math.round(match.similarity * 100)}% similar`;
  return `Already saved: ${what} (${why})`;
}

/** Skipped postings leave the form; this says which ones and why. */
function skippedNote(skipped: BatchItemResult[]) {
  const resumed = skipped.filter((r) => r.queued_job_id).length;
  const names = skipped
    .filter((r) => !r.queued_job_id)
    .map((r) => {
      const match = (r.details as { matches?: DuplicateMatch[] } | undefined)?.matches?.[0];
      return [match?.title, match?.company].filter(Boolean).join(" · ") || "an earlier job";
    });
  const parts = [];
  if (names.length) parts.push(`${names.length} skipped — already in your history (${names.slice(0, 3).join("; ")}${names.length > 3 ? "; …" : ""}).`);
  if (resumed) parts.push(`${resumed} you saved earlier ${resumed === 1 ? "is" : "are"} being generated now.`);
  return parts.join(" ");
}

export function BatchJobForm() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [entries, setEntries] = useState<Entry[]>([blank(), blank()]);
  const [busy, setBusy] = useState<null | "save" | "generate">(null);
  const [showErrors, setShowErrors] = useState(false);
  // "Save duplicates anyway" repeats whichever action the operator chose.
  const [lastGenerate, setLastGenerate] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const filled = entries.filter((e) => !isBlank(e));
  const update = (key: string, changes: Partial<Entry>) => setEntries((list) => list.map((e) => (e.key === key ? { ...e, ...changes, problem: undefined } : e)));
  const remove = (key: string) => setEntries((list) => (list.length > 1 ? list.filter((e) => e.key !== key) : [blank()]));
  const hasDuplicates = entries.some((e) => e.problem?.kind === "duplicate");

  const importFiles = async (files: FileList) => {
    const parsed: Entry[] = [];
    for (const file of Array.from(files)) {
      const text = await file.text();
      parsed.push({ ...blank(), ...parseJobText(text) });
    }
    setEntries((list) => [...list.filter((e) => !isBlank(e)), ...parsed]);
    toast.success(`${parsed.length} job description${parsed.length === 1 ? "" : "s"} added from files`);
  };

  const submit = async (generate: boolean, confirmDuplicates = false) => {
    setShowErrors(true);
    const toSend = confirmDuplicates ? filled.filter((e) => e.problem?.kind === "duplicate") : filled;
    if (!toSend.length) {
      toast.error("Add at least one job description");
      return;
    }
    if (toSend.some((e) => localProblem(e))) return;
    setLastGenerate(generate);
    setBusy(generate ? "generate" : "save");
    try {
      const response = await api.jobs.saveMany(
        toSend.map((e) => ({ job_url: e.job_url.trim(), raw_jd: e.raw_jd, company: e.company.trim() || undefined, title: e.title.trim() || undefined })),
        { process: generate, confirm_duplicates: confirmDuplicates },
      );
      const byKey = new Map(response.results.map((r) => [toSend[r.index].key, r]));
      const created = response.results.filter((r) => r.status === "created").length;
      const skipped = response.results.filter((r) => r.status === "skipped");
      const settled = (e: Entry) => ["created", "skipped"].includes(byKey.get(e.key)?.status ?? "");
      setEntries((list) => {
        const left = list
          .filter((e) => !settled(e))
          .map((e) => {
            const result = byKey.get(e.key);
            if (!result) return e;
            return { ...e, problem: { kind: result.status === "duplicate" ? ("duplicate" as const) : ("invalid" as const), message: result.status === "duplicate" ? duplicateMessage(result) : result.message ?? "Invalid" } };
          });
        return left.length ? left : [blank()];
      });
      void client.invalidateQueries({ queryKey: queryKeys.jobs });
      void client.invalidateQueries({ queryKey: queryKeys.dashboard });
      const problems = response.results.length - created - skipped.length;
      const description = [skippedNote(skipped), problems ? `${problems} need${problems === 1 ? "s" : ""} attention below.` : ""].filter(Boolean).join(" ") || undefined;
      if (created) {
        toast.success(
          generate
            ? `${created} job${created === 1 ? "" : "s"} saved and queued — tailored documents will appear in the Review Queue`
            : `${created} job${created === 1 ? "" : "s"} saved for later`,
          { description },
        );
      } else if (skipped.some((r) => r.queued_job_id)) {
        toast.success("Generating the jobs you saved earlier", { description });
      } else if (skipped.length) {
        toast.info("Nothing new to save", { description });
      }
      if (!problems) navigate(generate ? "/jobs" : "/jobs?filter=saved");
    } catch (error) {
      toast.error("Couldn't save the jobs", { description: describeError(error) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Add as many job descriptions as you like. Save them now and generate later from the Jobs page, or save and generate them all in one go.
      </p>
      {entries.map((entry, index) => {
        const problem = entry.problem?.message ?? (showErrors && !isBlank(entry) ? localProblem(entry) : null);
        return (
          <Card key={entry.key} className={cn("p-4", problem && "border-destructive/40")}>
            <div className="mb-3 flex items-center gap-2">
              <span className="text-sm font-semibold">Job {index + 1}</span>
              {problem && <span className={cn("text-xs", entry.problem?.kind === "duplicate" ? "text-warning" : "text-destructive")}>{problem}</span>}
              <Button variant="ghost" size="icon-sm" className="ml-auto text-muted-foreground hover:text-destructive" onClick={() => remove(entry.key)} aria-label={`Remove job ${index + 1}`}>
                <Trash2 />
              </Button>
            </div>
            <div className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr]">
              <Input value={entry.job_url} onChange={(e) => update(entry.key, { job_url: e.target.value })} placeholder="https://careers.example.com/jobs/123" aria-label={`Job ${index + 1} posting URL`} />
              <Input value={entry.company} onChange={(e) => update(entry.key, { company: e.target.value })} placeholder="Company (optional)" aria-label={`Job ${index + 1} company`} />
              <Input value={entry.title} onChange={(e) => update(entry.key, { title: e.target.value })} placeholder="Title (optional)" aria-label={`Job ${index + 1} title`} />
            </div>
            <Textarea
              className="mt-2"
              rows={6}
              value={entry.raw_jd}
              onChange={(e) => update(entry.key, { raw_jd: e.target.value })}
              placeholder="Paste the complete job description."
              aria-label={`Job ${index + 1} description`}
            />
          </Card>
        );
      })}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={() => setEntries((list) => [...list, blank()])}>
          <Plus /> Add another job
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept=".txt,.md,text/plain"
          multiple
          hidden
          aria-label="Choose job description text files"
          onChange={(event) => {
            if (event.target.files?.length) void importFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <Button variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
          <FileUp /> Import .txt files
        </Button>
        <span className="text-xs text-muted-foreground">One job per file; a URL on the first line is picked up automatically.</span>
      </div>

      {hasDuplicates && (
        <Alert
          variant="warning"
          title="Some jobs look like ones you already saved"
          action={
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void submit(lastGenerate, true)}>
              {lastGenerate ? "Save & generate duplicates anyway" : "Save duplicates anyway"}
            </Button>
          }
        >
          Remove them, or save them anyway if they are genuinely different postings.
        </Alert>
      )}

      <Card className="sticky bottom-4 z-10 flex-row flex-wrap items-center gap-3 px-4 py-3 shadow-lg">
        <span className="flex-1 text-sm">
          <span className="font-semibold tabular-nums">{filled.length}</span> job{filled.length === 1 ? "" : "s"} ready
        </span>
        <Button variant="outline" disabled={busy !== null || !filled.length} onClick={() => void submit(false)}>
          {busy === "save" ? <LoaderCircle className="animate-spin" /> : <Save />} Save {filled.length || ""} for later
        </Button>
        <Button disabled={busy !== null || !filled.length} onClick={() => void submit(true)}>
          {busy === "generate" ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Save & generate all
        </Button>
      </Card>
    </div>
  );
}
