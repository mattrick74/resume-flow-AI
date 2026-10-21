import { Badge } from "@/components/ui/badge";
import type { PipelineStatus } from "@/lib/types";

type Variant = "secondary" | "outline" | "success" | "warning" | "destructive";

const STATUS_DISPLAY: Record<PipelineStatus, { label: string; variant: Variant }> = {
  DRAFT: { label: "Draft", variant: "outline" },
  ANALYZING: { label: "Analyzing", variant: "secondary" },
  MATCHED: { label: "Matched", variant: "secondary" },
  GENERATING: { label: "Generating", variant: "secondary" },
  VALIDATING: { label: "Validating", variant: "secondary" },
  AWAITING_REVIEW: { label: "Preparing files", variant: "secondary" },
  APPROVED: { label: "Export queued", variant: "secondary" },
  EXPORTED: { label: "Exported", variant: "success" },
  NO_ELIGIBLE_CANDIDATE: { label: "No eligible candidate", variant: "outline" },
  FAILED: { label: "Failed", variant: "destructive" },
  SUBMITTED: { label: "Submitted", variant: "success" },
  REJECTED: { label: "Rejected", variant: "outline" },
  INTERVIEW: { label: "Interview", variant: "success" },
  OFFER: { label: "Offer", variant: "success" },
};

/** A job in DRAFT has been saved for later rather than abandoned mid-edit, so it reads "Saved". */
export function StatusBadge({ status, subject = "application" }: { status: PipelineStatus; subject?: "job" | "application" }) {
  const { label, variant } = subject === "job" && status === "DRAFT" ? { label: "Saved", variant: "outline" as const } : STATUS_DISPLAY[status];
  return <Badge variant={variant}>{label}</Badge>;
}
