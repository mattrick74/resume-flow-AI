import { CircleCheck, CircleDashed, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useVerificationRequired } from "@/lib/queries";
import type { Readiness } from "@/lib/types";

export function ReadinessBadge({ readiness, consent }: { readiness: Readiness; consent: boolean }) {
  if (readiness.ready_for_matching) {
    return (
      <Badge variant="success">
        <CircleCheck /> Ready
      </Badge>
    );
  }
  if (!consent) {
    return (
      <Badge variant="warning">
        <ShieldAlert /> Authorization needed
      </Badge>
    );
  }
  return (
    <Badge variant="outline">
      <CircleDashed /> {readiness.blockers.some((b) => b.startsWith("No work experience")) ? "Needs experience" : "Needs review"}
    </Badge>
  );
}

export function SkillCounts({ skills }: { skills: Record<string, number> }) {
  const required = useVerificationRequired();
  const verified = skills.verified ?? 0;
  const unverified = skills.unverified ?? 0;
  if (!required) return <span className="text-xs tabular-nums">{verified + unverified} skills</span>;
  return (
    <span className="inline-flex items-center gap-2 text-xs tabular-nums">
      <span className="text-success">{verified} verified</span>
      {unverified > 0 && <span className="text-muted-foreground">· {unverified} to review</span>}
    </span>
  );
}
