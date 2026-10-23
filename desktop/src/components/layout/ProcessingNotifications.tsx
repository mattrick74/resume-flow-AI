import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { sendQueueIdleNotification } from "@/lib/nativeNotifications";
import { queryKeys } from "@/lib/queries";
import type { ProcessingStageEvent } from "@/lib/types";
import { formatDuration } from "@/lib/utils";

const POLL_INTERVAL_MS = 2_000;

const STAGE_LABELS: Record<string, string> = {
  jd_analysis: "Job analysis",
  candidate_matching: "Candidate matching",
  resume_writing: "Resume writing",
  cover_letter_writing: "Cover letter writing",
  resume_render: "Resume file creation",
  cover_letter_render: "Cover letter file creation",
  pdf_conversion: "PDF conversion",
  document_export: "File export",
};

function detail(event: ProcessingStageEvent, key: string) {
  const value = event.details?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function subject(event: ProcessingStageEvent) {
  const candidate = detail(event, "candidate_name");
  const title = detail(event, "job_title");
  const company = detail(event, "company");
  const job = title && company ? `${title} · ${company}` : title ?? company;
  if (candidate && job) return `${candidate} · ${job}`;
  if (candidate) return candidate;
  if (job) return job;
  if (event.job_id) return `Job ${event.job_id}`;
  if (event.application_id) return `Application ${event.application_id}`;
  return "Background task";
}

function title(event: ProcessingStageEvent) {
  const stage = STAGE_LABELS[event.stage];
  if (!stage) return event.message.trim() || "Background task finished";
  if (event.status === "failed") return `${stage} failed`;
  if (event.status === "skipped") return `${stage} skipped`;
  return `${stage} complete`;
}

function notify(event: ProcessingStageEvent) {
  const source = detail(event, "source");
  const sourceLabel =
    event.stage === "jd_analysis"
      ? source === "heuristic"
        ? "fast local"
        : source === "ai"
          ? "AI fallback"
          : source === "cache"
            ? "cached"
            : null
      : null;
  const options = {
    id: `processing-event-${event.id}`,
    description: `${subject(event)} · ${formatDuration(event.duration_ms)}${sourceLabel ? ` · ${sourceLabel}` : ""}`,
  };
  if (event.status === "failed") toast.error(title(event), options);
  else if (event.status === "skipped") toast.info(title(event), options);
  else toast.success(title(event), options);
}

/**
 * Watches the durable processing-event feed once for the whole app. The first
 * request deliberately has no cursor: the backend returns an empty baseline at
 * its current event ID, so reopening the app never replays historical toasts.
 */
export function ProcessingNotifications() {
  const cursor = useRef<number | null>(null);
  const idleConfirmationPending = useRef(false);
  const client = useQueryClient();
  const feed = useQuery({
    queryKey: queryKeys.processingEvents,
    queryFn: () => api.processing.events(cursor.current ?? undefined),
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: true,
    retry: false,
    staleTime: 0,
  });

  useEffect(() => {
    if (!feed.data) return;
    const previous = cursor.current;
    const events = [...feed.data.events].sort((a, b) => a.id - b.id);
    const lastEventId = events.at(-1)?.id ?? 0;
    cursor.current = Math.max(previous ?? 0, feed.data.next_after_id, lastEventId);

    // The no-cursor response establishes the baseline and must stay silent.
    if (previous === null) return;
    const unseen = events.filter((event) => event.id > previous);
    const queueFinished = unseen.some((event) => event.stage === "queue_idle");
    for (const event of unseen) {
      if (event.stage !== "queue_idle") notify(event);
    }

    // Confirm the queue is still empty on the following poll. This collapses
    // fast parent/child or batch transitions into one true end-of-queue notice.
    if (feed.data.queue_depth > 0) {
      idleConfirmationPending.current = false;
    } else if (queueFinished) {
      idleConfirmationPending.current = true;
    } else if (idleConfirmationPending.current) {
      idleConfirmationPending.current = false;
      void sendQueueIdleNotification();
      void client.invalidateQueries({ queryKey: queryKeys.jobs });
      void client.invalidateQueries({ queryKey: queryKeys.applications });
      void client.invalidateQueries({ queryKey: queryKeys.dashboard });
    }
  }, [client, feed.data]);

  return null;
}
