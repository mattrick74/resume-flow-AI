import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";
import type { ReactNode } from "react";

import { IssueIcon, issuesAt } from "@/components/review/IssueList";
import { Button } from "@/components/ui/button";
import { ChipsInput } from "@/components/ui/chips-input";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useSelectedTemplateStyle } from "@/lib/queries";
import { headingStyle, mutedStyle, nameStyle, paperStyle, PX_PER_PT } from "@/lib/templateStyle";
import type { Issue, ResumeContent, TemplateStyle } from "@/lib/types";
import { cn, formatDateRange } from "@/lib/utils";

/** Paper-style preview; mirrors the DOCX template's structure and the selected template's look. */
export function Paper({ children, docStyle }: { children: ReactNode; docStyle: TemplateStyle }) {
  return (
    <div className="mx-auto max-w-[720px] rounded-md bg-white leading-relaxed shadow-sm ring-1 ring-black/10" style={paperStyle(docStyle)}>
      {children}
    </div>
  );
}

/** Name, optional headline and contact line, aligned like the template. */
export function DocHeader({ docStyle, name, headline, contact, nameScale = 1 }: { docStyle: TemplateStyle; name: string; headline?: ReactNode; contact: string[]; nameScale?: number }) {
  return (
    <div style={{ textAlign: docStyle.name_align }}>
      <div style={nameStyle(docStyle, nameScale)}>{name}</div>
      {headline && <div style={{ fontSize: (docStyle.size + 1) * PX_PER_PT }}>{headline}</div>}
      {contact.length > 0 && (
        <div style={{ ...mutedStyle(docStyle), fontSize: (docStyle.size - 1) * PX_PER_PT }}>{contact.join("  |  ")}</div>
      )}
    </div>
  );
}

function Heading({ children, docStyle }: { children: ReactNode; docStyle: TemplateStyle }) {
  return (
    <h3 className="mt-4 mb-1.5 pb-0.5" style={headingStyle(docStyle)}>
      {children}
    </h3>
  );
}

function Flags({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null;
  return (
    <span className="ml-1 inline-flex gap-0.5 align-middle" title={issues.map((i) => i.message).join("\n")}>
      {issues.slice(0, 2).map((issue, index) => (
        <IssueIcon key={index} severity={issue.severity} />
      ))}
    </span>
  );
}

export function ResumePreview({ content, issues, docStyle }: { content: ResumeContent; issues: Issue[]; docStyle?: TemplateStyle }) {
  const selected = useSelectedTemplateStyle();
  const style = docStyle ?? selected;
  const contact = [content.contact.location, content.contact.phone, content.contact.email, ...content.contact.links].filter((x): x is string => Boolean(x));
  return (
    <Paper docStyle={style}>
      <DocHeader
        docStyle={style}
        name={content.contact.name}
        contact={contact}
        headline={
          content.headline && (
            <>
              {content.headline}
              <Flags issues={issuesAt(issues, "headline")} />
            </>
          )
        }
      />
      {content.summary && (
        <>
          <Heading docStyle={style}>Summary</Heading>
          <p className={cn(issuesAt(issues, "summary").some((i) => i.severity === "error") && "bg-red-50")}>
            {content.summary}
            <Flags issues={issuesAt(issues, "summary")} />
          </p>
        </>
      )}
      {content.skills.length > 0 && (
        <>
          <Heading docStyle={style}>Skills</Heading>
          <p>
            {content.skills.join(", ")}
            <Flags issues={issuesAt(issues, "skills")} />
          </p>
        </>
      )}
      {content.experience.length > 0 && <Heading docStyle={style}>Experience</Heading>}
      {content.experience.map((role, r) => (
        <div key={role.role_id} className="mt-2">
          <div className="flex gap-2">
            <span>
              <span className="font-bold">{role.title}</span>
              <span>{"  ·  "}{[role.employer, role.location].filter(Boolean).join(", ")}</span>
              <Flags issues={issuesAt(issues, `experience[${r}]`)} />
            </span>
            <span className="ml-auto shrink-0" style={mutedStyle(style)}>{formatDateRange(role.start, role.end)}</span>
          </div>
          <ul className="mt-0.5 list-disc pl-5">
            {role.bullets.map((bullet, b) => {
              const own = issuesAt(issues, `experience[${r}].bullets[${b}]`);
              return (
                <li key={b} className={cn(own.some((i) => i.severity === "error") && "bg-red-50", own.some((i) => i.severity === "warning") && "bg-amber-50")}>
                  {bullet.text}
                  <Flags issues={own} />
                </li>
              );
            })}
          </ul>
          {(role.technologies?.length ?? 0) > 0 && (
            <p className="mt-0.5" aria-label={`Technologies at ${role.employer}`}>
              <span className="font-bold">Technologies: </span>
              {role.technologies?.join(", ")}
            </p>
          )}
        </div>
      ))}
      {content.education.length > 0 && (
        <>
          <Heading docStyle={style}>Education</Heading>
          {content.education.map((e, i) => (
            <p key={i}>
              {[[e.degree, e.field].filter(Boolean).join(", "), e.institution].filter(Boolean).join(" — ")}
              {e.end_year ? ` (${e.end_year})` : ""}
            </p>
          ))}
        </>
      )}
      {content.certifications.length > 0 && (
        <>
          <Heading docStyle={style}>Certifications</Heading>
          <ul className="list-disc pl-5">
            {content.certifications.map((c, i) => (
              <li key={i}>{[c.name, c.issuer, c.year].filter(Boolean).join(", ")}</li>
            ))}
          </ul>
        </>
      )}
    </Paper>
  );
}

export function ResumeEditor({ value, onChange }: { value: ResumeContent; onChange: (value: ResumeContent) => void }) {
  const setRole = (r: number, bullets: ResumeContent["experience"][number]["bullets"]) =>
    onChange({ ...value, experience: value.experience.map((role, i) => (i === r ? { ...role, bullets } : role)) });
  return (
    <div className="space-y-4 p-1">
      <p className="text-xs text-muted-foreground">Employers, titles, dates, education and credentials come from the candidate's profile and can't be edited here.</p>
      <div className="space-y-1.5">
        <label className="text-sm font-medium" htmlFor="ed-headline">
          Headline
        </label>
        <Input id="ed-headline" value={value.headline ?? ""} onChange={(e) => onChange({ ...value, headline: e.target.value || null })} />
      </div>
      <div className="space-y-1.5">
        <label className="text-sm font-medium" htmlFor="ed-summary">
          Summary
        </label>
        <Textarea id="ed-summary" rows={4} value={value.summary} onChange={(e) => onChange({ ...value, summary: e.target.value })} />
      </div>
      <div className="space-y-1.5">
        <span className="text-sm font-medium">Skills (order matters)</span>
        <ChipsInput ariaLabel="Resume skills" values={value.skills} onChange={(skills) => onChange({ ...value, skills })} />
      </div>
      {value.experience.map((role, r) => (
        <div key={role.role_id} className="rounded-lg border p-3">
          <div className="mb-2 text-sm font-semibold">
            {role.title} · <span className="font-normal text-muted-foreground">{role.employer}</span>
          </div>
          <div className="space-y-2">
            {role.bullets.map((bullet, b) => (
              <div key={b} className="flex items-start gap-1.5">
                <Textarea
                  rows={2}
                  className="min-h-14 flex-1 text-sm"
                  value={bullet.text}
                  onChange={(e) => setRole(r, role.bullets.map((x, i) => (i === b ? { ...x, text: e.target.value } : x)))}
                  aria-label={`Bullet ${b + 1} for ${role.employer}`}
                />
                <div className="flex flex-col">
                  <Button variant="ghost" size="icon-sm" disabled={b === 0} aria-label="Move up" onClick={() => setRole(r, swap(role.bullets, b, b - 1))}>
                    <ArrowUp />
                  </Button>
                  <Button variant="ghost" size="icon-sm" disabled={b === role.bullets.length - 1} aria-label="Move down" onClick={() => setRole(r, swap(role.bullets, b, b + 1))}>
                    <ArrowDown />
                  </Button>
                  <Button variant="ghost" size="icon-sm" className="hover:text-destructive" aria-label="Remove bullet" onClick={() => setRole(r, role.bullets.filter((_, i) => i !== b))}>
                    <Trash2 />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function swap<T>(items: T[], a: number, b: number): T[] {
  const copy = [...items];
  [copy[a], copy[b]] = [copy[b], copy[a]];
  return copy;
}
