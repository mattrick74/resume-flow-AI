import { BadgeCheck, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import type { CandidateProfile } from "@/lib/types";

export interface Unverified {
  education: string[];
  certifications: string[];
  summary: string | null;
  roles: number;
  bullets: number;
}

/** Facts that were extracted but not ticked, so they'd be left out of every resume. */
export function unverifiedFacts(profile: CandidateProfile): Unverified {
  const education = profile.education
    .filter((e) => !e.verified)
    .map((e) => [[e.degree, e.field].filter(Boolean).join(", "), e.institution].filter(Boolean).join(" — ") + (e.end_year ? ` (${e.end_year})` : ""));
  const certifications = profile.certifications.filter((c) => !c.verified).map((c) => c.name);
  return {
    education,
    certifications,
    summary: profile.summary && !profile.summary_verified ? profile.summary : null,
    roles: profile.employment.filter((r) => !r.verified).length,
    bullets: profile.employment.reduce((n, r) => n + r.bullets.filter((b) => !b.verified).length, 0),
  };
}

export const hasUnverified = (u: Unverified) => u.education.length + u.certifications.length + u.roles + u.bullets > 0 || u.summary !== null;

/** Ticks everything listed (the operator confirmed it against the original resume). */
export function verifyAll(profile: CandidateProfile): CandidateProfile {
  return {
    ...profile,
    summary_verified: profile.summary ? true : profile.summary_verified,
    employment: profile.employment.map((r) => ({ ...r, verified: true, bullets: r.bullets.map((b) => ({ ...b, verified: true })) })),
    education: profile.education.map((e) => ({ ...e, verified: true })),
    certifications: profile.certifications.map((c) => ({ ...c, verified: true })),
  };
}

export function CompleteReviewDialog({
  open,
  unverified,
  busy,
  onOpenChange,
  onVerifyAndComplete,
  onCompleteAnyway,
}: {
  open: boolean;
  unverified: Unverified;
  busy: boolean;
  onOpenChange: (open: boolean) => void;
  onVerifyAndComplete: () => void;
  onCompleteAnyway: () => void;
}) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Some details aren't verified yet"
      description="Resumes only use verified facts, so these would be left out. Check them against the original resume, then verify them here."
      className="max-w-xl"
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onCompleteAnyway}>
            Complete without them
          </Button>
          <Button disabled={busy} onClick={onVerifyAndComplete}>
            {busy ? <LoaderCircle className="animate-spin" /> : <BadgeCheck />} Verify these and complete
          </Button>
        </>
      }
    >
      <ul className="space-y-2 text-sm" aria-label="Unverified details">
        {unverified.education.map((text) => (
          <li key={`e-${text}`}>
            <span className="font-medium">Education:</span> {text}
          </li>
        ))}
        {unverified.certifications.map((text) => (
          <li key={`c-${text}`}>
            <span className="font-medium">Certification:</span> {text}
          </li>
        ))}
        {unverified.summary && (
          <li>
            <span className="font-medium">Summary:</span> <span className="text-muted-foreground">{unverified.summary.slice(0, 160)}{unverified.summary.length > 160 ? "…" : ""}</span>
          </li>
        )}
        {(unverified.roles > 0 || unverified.bullets > 0) && (
          <li>
            <span className="font-medium">Experience:</span> {unverified.roles} role(s) and {unverified.bullets} bullet(s)
          </li>
        )}
      </ul>
    </Modal>
  );
}
