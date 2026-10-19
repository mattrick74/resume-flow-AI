import { CheckCheck, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useVerificationRequired } from "@/lib/queries";
import type { Role } from "@/lib/types";
import { cn, newId } from "@/lib/utils";

export function ExperienceEditor({ roles, onChange }: { roles: Role[]; onChange: (roles: Role[]) => void }) {
  const required = useVerificationRequired();
  const update = (index: number, changes: Partial<Role>) => onChange(roles.map((r, i) => (i === index ? { ...r, ...changes } : r)));
  const remove = (index: number) => onChange(roles.filter((_, i) => i !== index));
  const verifyAll = () => onChange(roles.map((r) => ({ ...r, verified: true, bullets: r.bullets.map((b) => ({ ...b, verified: true })) })));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex-1 text-sm text-muted-foreground">
          {required
            ? "Check each role's employer, title and dates, and each bullet against the original resume. Only checked items are used."
            : "Every role and bullet here is used for resumes. Add the projects and technologies you want tailored resumes to draw on."}
        </p>
        {required && roles.length > 0 && (
          <Button variant="outline" size="sm" onClick={verifyAll}>
            <CheckCheck /> Verify everything shown
          </Button>
        )}
      </div>
      {roles.length === 0 && <p className="rounded-lg border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">No roles were extracted. Add them from the original resume.</p>}
      {roles.map((role, index) => (
        <Card key={role.id} className={cn("p-4", required && role.verified && "border-success/40")}>
          <div className="mb-3 flex items-center gap-2">
            {required ? (
              <label className="flex items-center gap-2 text-sm font-medium">
                <Checkbox checked={role.verified} onCheckedChange={(v) => update(index, { verified: v === true })} aria-label="Role details verified" />
                Role details verified
              </label>
            ) : (
              <span className="text-sm font-medium">Role {index + 1}</span>
            )}
            <Button variant="ghost" size="icon-sm" className="ml-auto text-muted-foreground hover:text-destructive" onClick={() => remove(index)} aria-label="Remove role">
              <Trash2 />
            </Button>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[2fr_2fr_1.5fr_1fr_1fr]">
            <Input value={role.title} onChange={(e) => update(index, { title: e.target.value })} placeholder="Title" aria-label="Title" />
            <Input value={role.employer} onChange={(e) => update(index, { employer: e.target.value })} placeholder="Employer" aria-label="Employer" />
            <Input value={role.location ?? ""} onChange={(e) => update(index, { location: e.target.value || null })} placeholder="Location" aria-label="Location" />
            <Input value={role.start ?? ""} onChange={(e) => update(index, { start: e.target.value || null })} placeholder="Start YYYY-MM" aria-label="Start date" />
            <Input value={role.end ?? ""} onChange={(e) => update(index, { end: e.target.value || null })} placeholder="End or present" aria-label="End date" />
          </div>
          <ul className="mt-3 space-y-2">
            {role.bullets.map((bullet, b) => (
              <li key={bullet.id} className="flex items-start gap-2">
                {required && (
                  <Checkbox
                    className="mt-2.5"
                    checked={bullet.verified}
                    onCheckedChange={(v) => update(index, { bullets: role.bullets.map((x, i) => (i === b ? { ...x, verified: v === true } : x)) })}
                    aria-label="Bullet verified"
                  />
                )}
                <Textarea
                  value={bullet.text}
                  rows={1}
                  className="min-h-9 resize-y py-1.5"
                  onChange={(e) => update(index, { bullets: role.bullets.map((x, i) => (i === b ? { ...x, text: e.target.value } : x)) })}
                  aria-label="Bullet text"
                />
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="mt-0.5 text-muted-foreground hover:text-destructive"
                  onClick={() => update(index, { bullets: role.bullets.filter((_, i) => i !== b) })}
                  aria-label="Remove bullet"
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => update(index, { bullets: [...role.bullets, { id: newId(), text: "", verified: false }] })}
          >
            <Plus /> Add bullet
          </Button>
        </Card>
      ))}
      <Button
        variant="outline"
        size="sm"
        onClick={() => onChange([...roles, { id: newId(), employer: "", title: "", location: null, start: null, end: null, bullets: [], verified: false }])}
      >
        <Plus /> Add role
      </Button>
    </div>
  );
}
