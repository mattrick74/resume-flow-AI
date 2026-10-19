import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useVerificationRequired } from "@/lib/queries";
import type { Certification, Education } from "@/lib/types";
import { newId } from "@/lib/utils";

function yearValue(value: string): number | null {
  const year = Number(value);
  return value.trim() && Number.isInteger(year) && year >= 1900 && year <= 2100 ? year : null;
}

export function CredentialsPanel({
  education,
  certifications,
  onEducation,
  onCertifications,
}: {
  education: Education[];
  certifications: Certification[];
  onEducation: (items: Education[]) => void;
  onCertifications: (items: Certification[]) => void;
}) {
  const required = useVerificationRequired();
  const editEducation = (index: number, changes: Partial<Education>) => onEducation(education.map((e, i) => (i === index ? { ...e, ...changes } : e)));
  const editCert = (index: number, changes: Partial<Certification>) => onCertifications(certifications.map((c, i) => (i === index ? { ...c, ...changes } : c)));

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="space-y-3 p-5">
        <h3 className="text-sm font-semibold">Education</h3>
        {education.length === 0 && <p className="text-sm text-muted-foreground">None recorded.</p>}
        {education.map((item, index) => (
          <div key={item.id} className="flex items-start gap-2">
            {required && <Checkbox className="mt-2.5" checked={item.verified} onCheckedChange={(v) => editEducation(index, { verified: v === true })} aria-label="Education verified" />}
            <div className="grid flex-1 gap-2 sm:grid-cols-[2fr_1.5fr_1.5fr_0.8fr]">
              <Input value={item.institution} onChange={(e) => editEducation(index, { institution: e.target.value })} placeholder="Institution" aria-label="Institution" />
              <Input value={item.degree ?? ""} onChange={(e) => editEducation(index, { degree: e.target.value || null })} placeholder="Degree" aria-label="Degree" />
              <Input value={item.field ?? ""} onChange={(e) => editEducation(index, { field: e.target.value || null })} placeholder="Field" aria-label="Field of study" />
              <Input
                defaultValue={item.end_year ?? ""}
                onBlur={(e) => editEducation(index, { end_year: yearValue(e.target.value) })}
                placeholder="Year"
                inputMode="numeric"
                aria-label="Graduation year"
              />
            </div>
            <Button variant="ghost" size="icon-sm" className="mt-0.5 text-muted-foreground hover:text-destructive" onClick={() => onEducation(education.filter((_, i) => i !== index))} aria-label="Remove education">
              <Trash2 />
            </Button>
          </div>
        ))}
        <Button variant="ghost" size="sm" onClick={() => onEducation([...education, { id: newId(), institution: "", degree: null, field: null, end_year: null, verified: false }])}>
          <Plus /> Add education
        </Button>
      </Card>

      <Card className="space-y-3 p-5">
        <div>
          <h3 className="text-sm font-semibold">Certifications and licenses</h3>
          <p className="text-xs text-muted-foreground">{required ? "Verified credentials" : "Credentials"} satisfy “required credential” gates.</p>
        </div>
        {certifications.length === 0 && <p className="text-sm text-muted-foreground">None recorded.</p>}
        {certifications.map((item, index) => (
          <div key={item.id} className="flex items-start gap-2">
            {required && <Checkbox className="mt-2.5" checked={item.verified} onCheckedChange={(v) => editCert(index, { verified: v === true })} aria-label="Certification verified" />}
            <div className="grid flex-1 gap-2 sm:grid-cols-[2.5fr_1.5fr_0.8fr]">
              <Input value={item.name} onChange={(e) => editCert(index, { name: e.target.value })} placeholder="Name" aria-label="Certification name" />
              <Input value={item.issuer ?? ""} onChange={(e) => editCert(index, { issuer: e.target.value || null })} placeholder="Issuer" aria-label="Issuer" />
              <Input defaultValue={item.year ?? ""} onBlur={(e) => editCert(index, { year: yearValue(e.target.value) })} placeholder="Year" inputMode="numeric" aria-label="Year" />
            </div>
            <Button variant="ghost" size="icon-sm" className="mt-0.5 text-muted-foreground hover:text-destructive" onClick={() => onCertifications(certifications.filter((_, i) => i !== index))} aria-label="Remove certification">
              <Trash2 />
            </Button>
          </div>
        ))}
        <Button variant="ghost" size="sm" onClick={() => onCertifications([...certifications, { id: newId(), name: "New certification", issuer: null, year: null, verified: false }])}>
          <Plus /> Add certification
        </Button>
      </Card>
    </div>
  );
}
