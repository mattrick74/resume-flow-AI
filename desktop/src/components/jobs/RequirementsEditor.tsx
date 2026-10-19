import { ChipsInput } from "@/components/ui/chips-input";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { useHardRequirementGates } from "@/lib/queries";
import type { JobRequirements } from "@/lib/types";

const SENIORITY: JobRequirements["seniority"][] = ["unknown", "intern", "junior", "mid", "senior", "lead", "principal", "executive"];
const ARRANGEMENT: JobRequirements["work_arrangement"][] = ["unknown", "onsite", "hybrid", "remote"];

export function RequirementsEditor({ value, onChange }: { value: JobRequirements; onChange: (value: JobRequirements) => void }) {
  const set = <K extends keyof JobRequirements>(key: K, v: JobRequirements[K]) => onChange({ ...value, [key]: v });
  // Hard requirements are only shown (and applied) when Settings → "Check hard requirements" is on.
  const gates = useHardRequirementGates();
  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label>Required skills</Label>
        <ChipsInput ariaLabel="Required skills" values={value.mandatory_skills} onChange={(v) => set("mandatory_skills", v)} placeholder="Type a skill and press Enter" />
      </div>
      <div className="space-y-1.5">
        <Label>Preferred skills</Label>
        <ChipsInput ariaLabel="Preferred skills" values={value.preferred_skills} onChange={(v) => set("preferred_skills", v)} placeholder="Nice-to-have skills" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="req-years">Min. years</Label>
          <Input
            id="req-years"
            inputMode="decimal"
            defaultValue={value.min_years_experience ?? ""}
            onBlur={(e) => set("min_years_experience", e.target.value.trim() ? Number(e.target.value) || null : null)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="req-seniority">Seniority</Label>
          <NativeSelect id="req-seniority" value={value.seniority} onChange={(e) => set("seniority", e.target.value as JobRequirements["seniority"])}>
            {SENIORITY.map((s) => (
              <option key={s} value={s}>
                {s === "unknown" ? "Not stated" : s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="req-arrangement">Arrangement</Label>
          <NativeSelect id="req-arrangement" value={value.work_arrangement} onChange={(e) => set("work_arrangement", e.target.value as JobRequirements["work_arrangement"])}>
            {ARRANGEMENT.map((a) => (
              <option key={a} value={a}>
                {a === "unknown" ? "Not stated" : a === "onsite" ? "On-site" : a[0].toUpperCase() + a.slice(1)}
              </option>
            ))}
          </NativeSelect>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="req-location">Location</Label>
          <Input id="req-location" value={value.location ?? ""} onChange={(e) => set("location", e.target.value || null)} placeholder="City, ST" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="req-industry">Industry</Label>
          <Input id="req-industry" value={value.industry ?? ""} onChange={(e) => set("industry", e.target.value || null)} />
        </div>
      </div>
      {gates && (
        <div className="rounded-lg border bg-muted/30 p-3">
          <div className="mb-2 text-xs font-medium text-muted-foreground">Hard requirements — pass/fail gates that scores can't offset</div>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="req-auth">Work authorization</Label>
              <Input id="req-auth" value={value.work_authorization ?? ""} onChange={(e) => set("work_authorization", e.target.value || null)} placeholder="e.g. Must be authorized to work in the US without sponsorship" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="req-clearance">Security clearance</Label>
              <Input id="req-clearance" value={value.security_clearance ?? ""} onChange={(e) => set("security_clearance", e.target.value || null)} placeholder="e.g. Active Secret clearance" />
            </div>
            <div className="space-y-1.5">
              <Label>Required licenses / certifications</Label>
              <ChipsInput ariaLabel="Required credentials" values={value.required_credentials} onChange={(v) => set("required_credentials", v)} placeholder="e.g. PMP" />
            </div>
            <div className="space-y-1.5">
              <Label>Other conditions (checked manually)</Label>
              <ChipsInput ariaLabel="Other hard requirements" values={value.other_hard_requirements} onChange={(v) => set("other_hard_requirements", v)} placeholder="e.g. Must travel 25%" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
