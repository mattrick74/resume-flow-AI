import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";

import { describeError } from "@/components/common/ErrorState";
import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import type { ImportResult } from "@/lib/types";

export type ImportItemStatus = "queued" | "uploading" | "done" | "duplicate" | "error";

export interface ImportItem {
  key: string;
  name: string;
  status: ImportItemStatus;
  message?: string;
  result?: ImportResult;
  candidateId?: string;
}

const ACCEPTED = /\.(docx|pdf)$/i;
const PARALLEL = 2;

/** Uploads resumes two at a time and tracks per-file progress. */
export function useResumeImport() {
  const client = useQueryClient();
  const [items, setItems] = useState<ImportItem[]>([]);
  const running = useRef(0);
  const pending = useRef<{ key: string; file: File }[]>([]);

  const update = (key: string, changes: Partial<ImportItem>) =>
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...changes } : item)));

  const pump = useCallback(() => {
    while (running.current < PARALLEL && pending.current.length) {
      const next = pending.current.shift()!;
      running.current += 1;
      update(next.key, { status: "uploading" });
      api.candidates
        .import(next.file)
        .then((result) => {
          const warnings = result.warnings.length ? ` · ${result.warnings.length} note(s)` : "";
          update(next.key, {
            status: "done",
            result,
            candidateId: result.candidate.id,
            message: `${result.candidate.full_name} · ${result.extraction_source === "ai" ? "AI" : "offline"} extraction${warnings}`,
          });
        })
        .catch((error: unknown) => {
          if (error instanceof ApiError && error.code === "duplicate_resume") {
            const details = error.details as { candidate_id?: string } | undefined;
            update(next.key, { status: "duplicate", message: error.message, candidateId: details?.candidate_id });
          } else {
            update(next.key, { status: "error", message: describeError(error) });
          }
        })
        .finally(() => {
          running.current -= 1;
          void client.invalidateQueries({ queryKey: queryKeys.candidates });
          void client.invalidateQueries({ queryKey: queryKeys.dashboard });
          pump();
        });
    }
  }, [client]);

  const add = useCallback(
    (files: FileList | File[]) => {
      const list = Array.from(files);
      const additions: ImportItem[] = list.map((file, index) => {
        const key = `${Date.now()}-${index}-${file.name}`;
        if (!ACCEPTED.test(file.name)) {
          return { key, name: file.name, status: "error", message: "Only .docx and .pdf files can be imported" };
        }
        pending.current.push({ key, file });
        return { key, name: file.name, status: "queued" };
      });
      setItems((current) => [...additions, ...current]);
      pump();
    },
    [pump],
  );

  const clearFinished = useCallback(() => setItems((current) => current.filter((i) => i.status === "queued" || i.status === "uploading")), []);
  const busy = items.some((i) => i.status === "queued" || i.status === "uploading");
  return { items, add, clearFinished, busy };
}
