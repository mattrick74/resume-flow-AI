import { Plus, Trash2 } from "lucide-react";

import { IssueIcon, issuesAt } from "@/components/review/IssueList";
import { DocHeader, Paper } from "@/components/review/ResumeDocument";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useSelectedTemplateStyle } from "@/lib/queries";
import type { CoverLetterContent, Issue, TemplateStyle } from "@/lib/types";
import { cn } from "@/lib/utils";

export function wordCount(content: CoverLetterContent) {
  return content.paragraphs.reduce((sum, p) => sum + (p.text.trim() ? p.text.trim().split(/\s+/).length : 0), 0);
}

export function CoverLetterPreview({ content, issues, docStyle }: { content: CoverLetterContent; issues: Issue[]; docStyle?: TemplateStyle }) {
  const selected = useSelectedTemplateStyle();
  const style = docStyle ?? selected;
  const contact = content.contact ? [content.contact.location, content.contact.phone, content.contact.email].filter((x): x is string => Boolean(x)) : [];
  return (
    <Paper docStyle={style}>
      <DocHeader docStyle={style} name={content.signature} contact={contact} />
      <p className="mt-6">{content.date}</p>
      {(content.company || content.job_title) && (
        <p className="mt-3">
          {content.company && <span className="font-bold">{content.company}</span>}
          {content.job_title && (
            <>
              <br />
              Re: {content.job_title}
            </>
          )}
        </p>
      )}
      <p className="mt-3">{content.greeting}</p>
      {content.paragraphs.map((paragraph, index) => {
        const own = issuesAt(issues, `paragraphs[${index}]`);
        return (
          <p key={index} className={cn("mt-3", own.some((i) => i.severity === "error") && "bg-red-50", own.some((i) => i.severity === "warning") && "bg-amber-50")}>
            {paragraph.text}
            {own.length > 0 && (
              <span className="ml-1 inline-flex gap-0.5 align-middle" title={own.map((i) => i.message).join("\n")}>
                {own.slice(0, 2).map((issue, i) => (
                  <IssueIcon key={i} severity={issue.severity} />
                ))}
              </span>
            )}
          </p>
        );
      })}
      <p className="mt-4">{content.closing}</p>
      <p className="mt-6">{content.signature}</p>
    </Paper>
  );
}

export function CoverLetterEditor({ value, onChange }: { value: CoverLetterContent; onChange: (value: CoverLetterContent) => void }) {
  return (
    <div className="space-y-3 p-1">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>About one page is 250–400 words.</span>
        <span className="tabular-nums">{wordCount(value)} words</span>
      </div>
      <div className="space-y-1.5">
        <label className="text-sm font-medium" htmlFor="cl-greeting">
          Greeting
        </label>
        <Input id="cl-greeting" value={value.greeting} onChange={(e) => onChange({ ...value, greeting: e.target.value })} />
      </div>
      {value.paragraphs.map((paragraph, index) => (
        <div key={index} className="flex items-start gap-1.5">
          <Textarea
            rows={5}
            className="flex-1 text-sm"
            value={paragraph.text}
            onChange={(e) => onChange({ ...value, paragraphs: value.paragraphs.map((p, i) => (i === index ? { ...p, text: e.target.value } : p)) })}
            aria-label={`Paragraph ${index + 1}`}
          />
          <Button variant="ghost" size="icon-sm" className="hover:text-destructive" aria-label="Remove paragraph" onClick={() => onChange({ ...value, paragraphs: value.paragraphs.filter((_, i) => i !== index) })}>
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button variant="ghost" size="sm" onClick={() => onChange({ ...value, paragraphs: [...value.paragraphs, { text: "", evidence_ids: [] }] })}>
        <Plus /> Add paragraph
      </Button>
      <div className="space-y-1.5">
        <label className="text-sm font-medium" htmlFor="cl-closing">
          Closing
        </label>
        <Input id="cl-closing" value={value.closing} onChange={(e) => onChange({ ...value, closing: e.target.value })} />
      </div>
    </div>
  );
}
