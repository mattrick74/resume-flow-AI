import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FolderOpen, LoaderCircle, Palette, RotateCcw, Sparkles } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { ReextractButton } from "@/components/candidates/Reextract";
import { describeError } from "@/components/common/ErrorState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { queryKeys, useCandidateMutation, useVerificationRequired } from "@/lib/queries";
import type { CandidateDetail, ResumeVersionInfo } from "@/lib/types";
import { formatRelativeTime } from "@/lib/utils";

const REVIEW_LABEL = { pending_review: "Pending review", partially_verified: "Partially verified", verified: "Verified" } as const;

export function VersionsPanel({ candidate }: { candidate: CandidateDetail }) {
  const [viewing, setViewing] = useState<ResumeVersionInfo | null>(null);
  const [applying, setApplying] = useState<ResumeVersionInfo | null>(null);
  const apply = useCandidateMutation(candidate.id, (versionId: string) => api.candidates.applyVersion(candidate.id, versionId));
  const navigate = useNavigate();
  const client = useQueryClient();
  const [copyingStyle, setCopyingStyle] = useState<string | null>(null);
  const required = useVerificationRequired();

  // Copies only this resume's look (fonts, colors, headings) into a reusable template.
  const copyStyle = async (version: ResumeVersionInfo) => {
    setCopyingStyle(version.id);
    try {
      const template = await api.templates.fromResume({ candidate_id: candidate.id, version_id: version.id });
      void client.invalidateQueries({ queryKey: queryKeys.templates });
      toast.success(`Saved the style as “${template.name}”`, {
        description: template.notes.length ? template.notes.join(" ") : "Choose it in Settings → Templates to use it for exports.",
        action: { label: "Open Templates", onClick: () => navigate("/settings#templates") },
      });
    } catch (error) {
      toast.error("Couldn't copy this resume's style", { description: describeError(error) });
    } finally {
      setCopyingStyle(null);
    }
  };

  return (
    <div className="space-y-3">
      {candidate.newer_extraction_available && (
        <p className="rounded-lg border border-primary/20 bg-accent/50 px-4 py-2.5 text-sm">
          A newer resume version was uploaded after this profile was {required ? "reviewed" : "set up"}. Its extracted details are stored below; use “Use this extraction” to replace the profile
          {required && " (you'll review it again)"}.
        </p>
      )}
      <ul className="divide-y rounded-lg border">
        {candidate.resume_versions.map((version) => (
          <li key={version.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <div className="flex size-9 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <FileText className="size-4" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-sm font-medium">
                Version {version.version}
                {required && <Badge variant="outline">{REVIEW_LABEL[version.verification_status]}</Badge>}
                {version.extraction_source === "ai" && (
                  <Badge variant="secondary">
                    <Sparkles /> AI extraction
                  </Badge>
                )}
                {candidate.profile.source_version_id === version.id && <Badge variant="success">Profile source</Badge>}
              </div>
              <div className="truncate text-xs text-muted-foreground">
                {version.original_filename} · uploaded {formatRelativeTime(version.created_at)}
              </div>
              {version.extraction_warnings.map((warning) => (
                <div key={warning} className="mt-1 text-xs text-warning">
                  {warning}
                </div>
              ))}
            </div>
            <Button variant="ghost" size="sm" onClick={() => setViewing(version)}>
              View text
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => api.system.open(version.original_file_path).catch((e) => toast.error("Couldn't open file", { description: describeError(e) }))}
            >
              <FolderOpen /> Open original
            </Button>
            <ReextractButton candidate={candidate} version={version} />
            <Button variant="ghost" size="sm" disabled={copyingStyle === version.id} onClick={() => void copyStyle(version)}>
              {copyingStyle === version.id ? <LoaderCircle className="animate-spin" /> : <Palette />} Use as template
            </Button>
            {candidate.profile.source_version_id !== version.id && (
              <Button variant="outline" size="sm" onClick={() => setApplying(version)}>
                <RotateCcw /> Use this extraction
              </Button>
            )}
          </li>
        ))}
      </ul>

      <ExtractedTextModal candidateId={candidate.id} version={viewing} onClose={() => setViewing(null)} />
      <Modal
        open={applying !== null}
        onOpenChange={(open) => !open && setApplying(null)}
        title={`Replace the profile with version ${applying?.version}'s extraction?`}
        description={
          required
            ? "Experience, education, certifications and summary will be replaced with unverified extracted values, and the profile review must be completed again. Skills are kept."
            : "Experience, education, certifications and summary will be replaced with this version's extracted values. Skills are kept."
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setApplying(null)}>
              Cancel
            </Button>
            <Button
              disabled={apply.isPending}
              onClick={() =>
                applying &&
                apply.mutate(applying.id, {
                  onSuccess: () => {
                    toast.success("Profile replaced; review it again");
                    setApplying(null);
                  },
                  onError: (e) => toast.error("Couldn't replace profile", { description: describeError(e) }),
                })
              }
            >
              Replace profile
            </Button>
          </>
        }
      />
    </div>
  );
}

function ExtractedTextModal({ candidateId, version, onClose }: { candidateId: string; version: ResumeVersionInfo | null; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["resume-version", version?.id],
    queryFn: () => api.candidates.version(candidateId, version!.id),
    enabled: version !== null,
  });
  return (
    <Modal open={version !== null} onOpenChange={(open) => !open && onClose()} title={`Extracted text — version ${version?.version}`} description={version?.original_filename} className="max-w-3xl">
      {detail.isPending ? (
        <Skeleton className="h-80" />
      ) : (
        <pre className="max-h-[60vh] overflow-auto rounded-md bg-muted p-4 font-sans text-xs leading-relaxed whitespace-pre-wrap">{detail.data?.extracted_text}</pre>
      )}
    </Modal>
  );
}
