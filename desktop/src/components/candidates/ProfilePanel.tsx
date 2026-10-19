import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useVerificationRequired } from "@/lib/queries";
import type { CandidatePreferences, CandidateProfile, WorkArrangement } from "@/lib/types";

const ARRANGEMENTS: { value: WorkArrangement; label: string }[] = [
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "On-site" },
];

function listToText(values: string[]) {
  return values.join(", ");
}

function textToList(value: string) {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

export function ProfilePanel({
  profile,
  preferences,
  onProfile,
  onPreferences,
}: {
  profile: CandidateProfile;
  preferences: CandidatePreferences;
  onProfile: (changes: Partial<CandidateProfile>) => void;
  onPreferences: (changes: Partial<CandidatePreferences>) => void;
}) {
  const required = useVerificationRequired();
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-sm font-semibold">Headline and summary</h3>
          <p className="text-xs text-muted-foreground">
            {required ? "Used as written only when verified; otherwise the summary is built from verified facts." : "A starting point: each tailored resume rewrites the summary for its job."}
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="headline">Headline</Label>
          <Input id="headline" value={profile.headline ?? ""} onChange={(e) => onProfile({ headline: e.target.value || null })} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="summary">Summary</Label>
          <Textarea id="summary" rows={5} value={profile.summary ?? ""} onChange={(e) => onProfile({ summary: e.target.value || null })} />
        </div>
        {required && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={profile.summary_verified} onCheckedChange={(v) => onProfile({ summary_verified: v === true })} />
            Headline and summary are accurate
          </label>
        )}
      </Card>

      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-sm font-semibold">Work authorization</h3>
          <p className="text-xs text-muted-foreground">
            {required ? "Only used for eligibility gates when supplied and verified." : "Used for eligibility gates when supplied."} Leave empty if unknown.
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="authorization">Status</Label>
          <Input
            id="authorization"
            value={profile.work_authorization ?? ""}
            onChange={(e) => onProfile({ work_authorization: e.target.value || null })}
            placeholder="e.g. U.S. Citizen · Permanent Resident · Requires H-1B sponsorship"
          />
        </div>
        {required && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={profile.work_authorization_verified}
              disabled={!profile.work_authorization}
              onCheckedChange={(v) => onProfile({ work_authorization_verified: v === true })}
            />
            Confirmed with the candidate
          </label>
        )}

        <div className="border-t pt-4">
          <h3 className="text-sm font-semibold">Preferences</h3>
          <p className="text-xs text-muted-foreground">Optional. Used as pass/fail gates for job arrangement and location.</p>
        </div>
        <fieldset className="space-y-1.5">
          <legend className="text-sm font-medium">Accepts work arrangements</legend>
          <div className="flex gap-4 pt-1">
            {ARRANGEMENTS.map((a) => (
              <label key={a.value} className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={preferences.work_arrangements.includes(a.value)}
                  onCheckedChange={(v) =>
                    onPreferences({
                      work_arrangements: v === true ? [...preferences.work_arrangements, a.value] : preferences.work_arrangements.filter((x) => x !== a.value),
                    })
                  }
                />
                {a.label}
              </label>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">Leave all unchecked if the candidate has no preference.</p>
        </fieldset>
        <div className="space-y-1.5">
          <Label htmlFor="locations">Preferred locations</Label>
          <Input
            id="locations"
            defaultValue={listToText(preferences.preferred_locations)}
            onBlur={(e) => onPreferences({ preferred_locations: textToList(e.target.value) })}
            placeholder="Austin, TX, Dallas, TX"
          />
        </div>
        <div className="flex items-center justify-between gap-4">
          <Label htmlFor="relocate">Willing to relocate</Label>
          <Switch id="relocate" checked={preferences.willing_to_relocate === true} onCheckedChange={(v) => onPreferences({ willing_to_relocate: v })} />
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="employment-types">Employment types</Label>
            <Input
              id="employment-types"
              defaultValue={listToText(preferences.employment_types)}
              onBlur={(e) => onPreferences({ employment_types: textToList(e.target.value) })}
              placeholder="Full-time, Contract"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="availability">Availability</Label>
            <Input id="availability" value={preferences.availability ?? ""} onChange={(e) => onPreferences({ availability: e.target.value || null })} placeholder="2 weeks notice" />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="pref-notes">Notes</Label>
          <Textarea id="pref-notes" rows={2} value={preferences.notes ?? ""} onChange={(e) => onPreferences({ notes: e.target.value || null })} />
        </div>
      </Card>
    </div>
  );
}
