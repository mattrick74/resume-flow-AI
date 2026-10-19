import { Archive, ArchiveRestore, ArrowLeft, BadgeCheck, BadgePlus, Briefcase, FileStack, GraduationCap, ListChecks, Pencil, Trash2, Upload, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { toast } from "sonner";

import { ReadinessBadge } from "@/components/candidates/CandidateBadges";
import { CredentialsPanel } from "@/components/candidates/CredentialsPanel";
import { ExperienceEditor } from "@/components/candidates/ExperienceEditor";
import { EvidencePanel } from "@/components/experience/EvidencePanel";
import { ProfilePanel } from "@/components/candidates/ProfilePanel";
import { SkillsPanel } from "@/components/candidates/SkillsPanel";
import { CompleteReviewDialog, hasUnverified, unverifiedFacts, verifyAll } from "@/components/candidates/CompleteReviewDialog";
import { VersionsPanel } from "@/components/candidates/VersionsPanel";
import { ErrorState, describeError } from "@/components/common/ErrorState";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { useCandidate, useCandidateMutation, useVerificationRequired } from "@/lib/queries";
import type { CandidateDetail, CandidatePreferences, CandidateProfile } from "@/lib/types";

export function CandidateDetailPage() {
  const { id = "" } = useParams();
  const candidate = useCandidate(id);
  if (candidate.isPending) return <Skeleton className="h-[600px]" />;
  if (candidate.isError) return <ErrorState error={candidate.error} onRetry={() => void candidate.refetch()} />;
  return <CandidateView key={candidate.data.id} candidate={candidate.data} />;
}

interface Draft {
  profile: CandidateProfile;
  preferences: CandidatePreferences;
}

function CandidateView({ candidate }: { candidate: CandidateDetail }) {
  const id = candidate.id;
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const required = useVerificationRequired();
  const saved = useMemo<Draft>(() => ({ profile: candidate.profile, preferences: candidate.preferences }), [candidate.profile, candidate.preferences]);
  const [draft, setDraft] = useState<Draft>(saved);
  const savedKey = JSON.stringify(saved);
  useEffect(() => setDraft(JSON.parse(savedKey) as Draft), [savedKey]);
  const dirty = JSON.stringify(draft) !== savedKey;

  const [editingContact, setEditingContact] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const onError = (title: string) => (error: unknown) => toast.error(title, { description: describeError(error) });

  const save = useCandidateMutation(id, (d: Draft) => api.candidates.update(id, { profile: d.profile, preferences: d.preferences }));
  const consent = useCandidateMutation(id, (value: boolean) => api.candidates.update(id, { consent_confirmed: value }));
  const review = useCandidateMutation(id, () => api.candidates.completeReview(id));
  const archive = useCandidateMutation(id, (status: "active" | "archived") => api.candidates.update(id, { status }));
  const upload = useCandidateMutation(id, (file: File) => api.candidates.addVersion(id, file).then((r) => r.candidate));

  const saveDraft = () => save.mutate(draft, { onSuccess: () => toast.success("Profile saved"), onError: onError("Profile not saved") });

  // Completing the review: anything still unverified is listed first, so nothing (like education) is left out by accident.
  const [confirmReview, setConfirmReview] = useState(false);
  const unverified = unverifiedFacts(candidate.profile);
  const completeReview = () =>
    review.mutate(undefined, {
      onSuccess: () => {
        setConfirmReview(false);
        toast.success("Review completed");
      },
      onError: onError("Couldn't complete review"),
    });
  const verifyAndComplete = () =>
    save.mutate({ profile: verifyAll(candidate.profile), preferences: candidate.preferences }, { onSuccess: completeReview, onError: onError("Couldn't verify") });

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && dirty) {
        event.preventDefault();
        saveDraft();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const setProfile = (changes: Partial<CandidateProfile>) => setDraft((d) => ({ ...d, profile: { ...d.profile, ...changes } }));
  const setPreferences = (changes: Partial<CandidatePreferences>) => setDraft((d) => ({ ...d, preferences: { ...d.preferences, ...changes } }));
  const toReview = required ? candidate.skills.filter((s) => s.verification_status === "unverified").length : 0;

  return (
    <div className="pb-20">
      <Link to="/candidates" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Candidates
      </Link>

      <div className="mb-5 flex flex-wrap items-start gap-4">
        <div className="flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <UserRound className="size-6" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">{candidate.full_name}</h1>
            {candidate.status === "archived" ? <span className="text-sm text-muted-foreground">(archived)</span> : <ReadinessBadge readiness={candidate.readiness} consent={candidate.consent_confirmed_at !== null} />}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {[candidate.email, candidate.phone, candidate.location].filter(Boolean).join(" · ") || "No contact details"}
            {" · "}
            {candidate.years_experience} yrs {required && "verified "}experience
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditingContact(true)}>
            <Pencil /> Edit details
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept=".docx,.pdf"
            hidden
            aria-label="Choose new resume version"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file)
                upload.mutate(file, {
                  onSuccess: (detail) => toast.success(`Version ${detail.resume_versions[0]?.version} uploaded`, { description: "New skills were added for review." }),
                  onError: onError("Upload failed"),
                });
            }}
          />
          <Button variant="outline" size="sm" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
            <Upload /> {upload.isPending ? "Uploading…" : "New resume version"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => archive.mutate(candidate.status === "active" ? "archived" : "active", { onError: onError("Couldn't update status") })}
          >
            {candidate.status === "active" ? <Archive /> : <ArchiveRestore />}
            {candidate.status === "active" ? "Archive" : "Restore"}
          </Button>
          <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive/10 hover:text-destructive" onClick={() => setConfirmDelete(true)}>
            <Trash2 /> Delete
          </Button>
        </div>
      </div>

      <div className="mb-5 grid gap-3 lg:grid-cols-[1fr_auto]">
        <Alert
          variant={candidate.readiness.ready_for_matching ? "info" : "warning"}
          title={candidate.readiness.ready_for_matching ? "Ready for matching" : "Not yet used for matching"}
        >
          {candidate.readiness.ready_for_matching
            ? `${candidate.readiness.verified_roles} ${required ? "verified " : ""}role(s) and ${candidate.readiness.verified_skills} ${required ? "verified " : ""}skill(s) will be used.`
            : candidate.readiness.blockers.join(" · ")}
          {(candidate.readiness.warnings ?? []).length > 0 && (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4" aria-label="Left out of resumes">
              {candidate.readiness.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </Alert>
        <div className="flex flex-col justify-center gap-2 rounded-lg border bg-card px-4 py-3">
          <label className="flex items-center justify-between gap-6 text-sm">
            <span>
              <span className="font-medium">Candidate authorization</span>
              <span className="block text-xs text-muted-foreground">They agreed to you using their data and applying for them</span>
            </span>
            <Switch
              checked={candidate.consent_confirmed_at !== null}
              onCheckedChange={(value) => consent.mutate(value, { onError: onError("Couldn't update authorization") })}
              aria-label="Candidate authorization confirmed"
            />
          </label>
          {required && (
            <Button
              size="sm"
              disabled={review.isPending || dirty}
              title={dirty ? "Save your changes first" : undefined}
              onClick={() => (hasUnverified(unverified) ? setConfirmReview(true) : completeReview())}
            >
              <BadgeCheck /> {candidate.profile_reviewed_at ? "Re-confirm review" : "Mark review complete"}
            </Button>
          )}
        </div>
      </div>

      <CompleteReviewDialog
        open={confirmReview}
        unverified={unverified}
        busy={save.isPending || review.isPending}
        onOpenChange={setConfirmReview}
        onVerifyAndComplete={verifyAndComplete}
        onCompleteAnyway={completeReview}
      />

      <Tabs defaultValue={params.get("tab") ?? "experience"}>
        <TabsList>
          <TabsTrigger value="experience">
            <Briefcase /> Experience
          </TabsTrigger>
          <TabsTrigger value="evidence">
            <BadgePlus /> Evidence
          </TabsTrigger>
          <TabsTrigger value="skills">
            <ListChecks /> Skills {toReview > 0 && <span className="rounded bg-warning/20 px-1.5 text-[11px] text-foreground">{toReview}</span>}
          </TabsTrigger>
          <TabsTrigger value="profile">
            <UserRound /> Summary & preferences
          </TabsTrigger>
          <TabsTrigger value="credentials">
            <GraduationCap /> Education & credentials
          </TabsTrigger>
          <TabsTrigger value="versions">
            <FileStack /> Resume versions
          </TabsTrigger>
        </TabsList>
        <TabsContent value="experience">
          <ExperienceEditor roles={draft.profile.employment} onChange={(employment) => setProfile({ employment })} />
        </TabsContent>
        <TabsContent value="evidence">
          <EvidencePanel candidate={candidate} />
        </TabsContent>
        <TabsContent value="skills">
          <SkillsPanel candidate={candidate} />
        </TabsContent>
        <TabsContent value="profile">
          <ProfilePanel profile={draft.profile} preferences={draft.preferences} onProfile={setProfile} onPreferences={setPreferences} />
        </TabsContent>
        <TabsContent value="credentials">
          <CredentialsPanel
            education={draft.profile.education}
            certifications={draft.profile.certifications}
            onEducation={(education) => setProfile({ education })}
            onCertifications={(certifications) => setProfile({ certifications })}
          />
        </TabsContent>
        <TabsContent value="versions">
          <VersionsPanel candidate={candidate} />
        </TabsContent>
      </Tabs>

      {dirty && (
        <div className="sticky bottom-4 z-10 mt-4 flex items-center gap-3 rounded-xl border bg-popover px-4 py-3 shadow-lg animate-in fade-in-0 slide-in-from-bottom-2">
          <span className="flex-1 text-sm">Unsaved profile changes</span>
          <Button variant="ghost" size="sm" onClick={() => setDraft(saved)}>
            Discard
          </Button>
          <Button size="sm" disabled={save.isPending} onClick={saveDraft}>
            {save.isPending ? "Saving…" : "Save changes"} <Kbd className="border-primary-foreground/20 bg-primary-foreground/10 text-primary-foreground">Ctrl S</Kbd>
          </Button>
        </div>
      )}

      <ContactModal candidate={candidate} open={editingContact} onOpenChange={setEditingContact} />
      <Modal
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={`Delete ${candidate.full_name}?`}
        description="This permanently removes the profile, its resume versions and stored original files. Candidates with applications can only be archived."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() =>
                api.candidates
                  .remove(id)
                  .then(() => {
                    toast.success("Candidate deleted");
                    navigate("/candidates");
                  })
                  .catch((error) => {
                    setConfirmDelete(false);
                    onError("Couldn't delete candidate")(error);
                  })
              }
            >
              Delete permanently
            </Button>
          </>
        }
      />
    </div>
  );
}

function ContactModal({ candidate, open, onOpenChange }: { candidate: CandidateDetail; open: boolean; onOpenChange: (open: boolean) => void }) {
  const [form, setForm] = useState({ full_name: candidate.full_name, email: candidate.email ?? "", phone: candidate.phone ?? "", location: candidate.location ?? "" });
  useEffect(() => {
    if (open) setForm({ full_name: candidate.full_name, email: candidate.email ?? "", phone: candidate.phone ?? "", location: candidate.location ?? "" });
  }, [open, candidate]);
  const save = useCandidateMutation(candidate.id, () =>
    api.candidates.update(candidate.id, { full_name: form.full_name, email: form.email || null, phone: form.phone || null, location: form.location || null }),
  );
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Edit contact details"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!form.full_name.trim() || save.isPending}
            onClick={() =>
              save.mutate(undefined, {
                onSuccess: () => {
                  toast.success("Details saved");
                  onOpenChange(false);
                },
                onError: (error) => toast.error("Details not saved", { description: describeError(error) }),
              })
            }
          >
            Save
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        {(
          [
            ["full_name", "Full name"],
            ["email", "Email"],
            ["phone", "Phone"],
            ["location", "Location"],
          ] as const
        ).map(([key, label]) => (
          <div key={key} className="space-y-1.5">
            <Label htmlFor={`contact-${key}`}>{label}</Label>
            <Input id={`contact-${key}`} value={form[key]} onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))} />
          </div>
        ))}
      </div>
    </Modal>
  );
}
