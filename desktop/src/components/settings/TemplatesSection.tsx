import { useQueryClient } from "@tanstack/react-query";
import { Check, FileUp, FileText, LoaderCircle, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { ResumePreview } from "@/components/review/ResumeDocument";
import { Section } from "@/components/settings/layout";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { api } from "@/lib/api";
import { queryKeys, useTemplates, useUpdateSettings } from "@/lib/queries";
import type { DocumentTemplate, ResumeContent, TemplateStyle } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Fictional content for template previews (matches the backend's sample documents). */
export const SAMPLE_RESUME: ResumeContent = {
  contact: { name: "Jordan Avery", email: "jordan.avery@example.com", phone: "(555) 010-4477", location: "Austin, TX", links: [] },
  headline: "Senior Data Engineer",
  summary: "Data engineer with 9 years building reliable batch and streaming pipelines on AWS for analytics and machine-learning teams.",
  skills: ["Python", "SQL", "Apache Spark", "Apache Airflow", "AWS", "dbt", "Snowflake", "Terraform", "Kafka"],
  experience: [
    {
      role_id: "R-1",
      employer: "Northwind Analytics Inc",
      title: "Senior Data Engineer",
      location: "Austin, TX",
      start: "2020-03",
      end: "present",
      bullets: [
        { text: "Built streaming pipelines with Kafka and Spark processing 2 billion events a day", source_ids: [] },
        { text: "Moved 40 nightly batch jobs to Airflow, cutting failures by 60%", source_ids: [] },
        { text: "Modeled the analytics warehouse in Snowflake with dbt and automated tests", source_ids: [] },
      ],
    },
    {
      role_id: "R-2",
      employer: "Bluebonnet Logistics LLC",
      title: "Data Engineer",
      location: "Dallas, TX",
      start: "2016-06",
      end: "2020-02",
      bullets: [
        { text: "Designed the shipment data lake on AWS S3 and Glue", source_ids: [] },
        { text: "Managed infrastructure as code with Terraform across three environments", source_ids: [] },
      ],
    },
  ],
  education: [{ degree: "B.S.", field: "Computer Science", institution: "University of Texas at Austin", end_year: 2016 }],
  certifications: [{ name: "AWS Certified Data Engineer – Associate", year: 2022 }],
};

const COMMON_FONTS = [
  "Aptos", "Arial", "Book Antiqua", "Calibri", "Cambria", "Century Gothic", "Constantia", "Corbel", "Franklin Gothic Book",
  "Garamond", "Georgia", "Gill Sans MT", "Helvetica", "Lato", "Open Sans", "Palatino Linotype", "Roboto", "Segoe UI",
  "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana",
];

/** A miniature page rendered with the template's style. */
export function TemplateThumbnail({ style, scale = 0.3, className }: { style: TemplateStyle; scale?: number; className?: string }) {
  return (
    <div className={cn("pointer-events-none relative overflow-hidden rounded-md bg-white ring-1 ring-black/10", className)} style={{ height: 980 * scale }} aria-hidden>
      <div className="absolute top-0 left-0 origin-top-left" style={{ width: 720, transform: `scale(${scale})` }}>
        <ResumePreview content={SAMPLE_RESUME} issues={[]} docStyle={style} />
      </div>
    </div>
  );
}

function useOpenSample() {
  const [busy, setBusy] = useState<string | null>(null);
  const open = async (template: DocumentTemplate) => {
    setBusy(template.key);
    try {
      const files = await api.templates.samples(template.key);
      await api.system.open(files.resume_pdf ?? files.resume_docx);
    } catch (error) {
      toast.error("Couldn't open the sample", { description: describeError(error) });
    } finally {
      setBusy(null);
    }
  };
  return { open, busy };
}

export function TemplatesSection() {
  const templates = useTemplates();
  const updateSettings = useUpdateSettings();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState<DocumentTemplate | null>(null);
  const [deleting, setDeleting] = useState<DocumentTemplate | null>(null);
  const [lastImport, setLastImport] = useState<DocumentTemplate | null>(null);
  const sample = useOpenSample();
  const refresh = () => client.invalidateQueries({ queryKey: queryKeys.templates });
  const { hash } = useLocation();
  useEffect(() => {
    if (hash === "#templates") document.getElementById("templates")?.scrollIntoView({ behavior: "smooth" });
  }, [hash]);

  const use = (template: DocumentTemplate) =>
    updateSettings.mutate(
      { resume_template: template.key },
      {
        onSuccess: () => toast.success(`New documents will use “${template.name}”`),
        onError: (error) => toast.error("Not saved", { description: describeError(error) }),
      },
    );

  const importFile = async (file: File) => {
    setImporting(true);
    try {
      const created = await api.templates.import(file);
      await refresh();
      setLastImport(created);
      toast.success(`Copied the style of ${file.name}`, { description: `Saved as “${created.name}”. Check it, then choose “Use this template”.` });
    } catch (error) {
      toast.error("Couldn't copy that resume's style", { description: describeError(error) });
    } finally {
      setImporting(false);
    }
  };

  const remove = async (template: DocumentTemplate) => {
    try {
      await api.templates.remove(template.id!);
      await Promise.all([refresh(), client.invalidateQueries({ queryKey: queryKeys.settings })]);
      if (lastImport?.id === template.id) setLastImport(null);
      toast.success(`Deleted “${template.name}”`);
    } catch (error) {
      toast.error("Not deleted", { description: describeError(error) });
    } finally {
      setDeleting(null);
    }
  };

  const data = templates.data;
  return (
    <Section
      id="templates"
      title="Templates"
      description="How resumes and cover letters look. Copy the style of any resume you like; the template in use applies to every export. Only the look is copied, never the text."
    >
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileInput}
          type="file"
          accept=".docx,.pdf"
          hidden
          aria-label="Choose a resume to copy the style from"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void importFile(file);
            event.target.value = "";
          }}
        />
        <Button variant="outline" size="sm" disabled={importing} onClick={() => fileInput.current?.click()}>
          {importing ? <LoaderCircle className="animate-spin" /> : <FileUp />} Copy style from a resume…
        </Button>
        <span className="text-xs text-muted-foreground">DOCX or text-based PDF. You can also use a candidate’s stored resume from their profile.</span>
      </div>

      {lastImport && lastImport.notes.length > 0 && (
        <Alert variant="warning" title={`Check “${lastImport.name}”`} action={<Button size="sm" variant="outline" onClick={() => setEditing(data?.templates.find((t) => t.id === lastImport.id) ?? lastImport)}>Adjust style</Button>}>
          {lastImport.notes.join(" ")}
        </Alert>
      )}

      {templates.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-80" />
          <Skeleton className="h-80" />
        </div>
      ) : templates.isError ? (
        <Alert variant="destructive" title="Templates couldn't load">
          {describeError(templates.error)}
        </Alert>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" role="list" aria-label="Document templates">
          {data!.templates.map((template) => {
            const selected = template.key === data!.selected;
            return (
              <Card key={template.key} role="listitem" aria-label={template.name} className={cn("gap-3 p-3", selected && "ring-2 ring-primary")}>
                <TemplateThumbnail style={template.style} scale={0.3} className="w-[216px] self-center" />
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                      <span className="min-w-0 text-sm font-medium break-words">{template.name}</span>
                      {selected && (
                        <Badge variant="success">
                          <Check /> In use
                        </Badge>
                      )}
                    </div>
                    <div className="truncate text-xs text-muted-foreground" title={template.source_filename ?? undefined}>
                      {template.builtin ? "Built-in" : `From ${template.source_filename ?? "a resume"}`} · {template.style.font} {template.style.size}pt
                    </div>
                  </div>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label={`More actions for ${template.name}`}>
                        {sample.busy === template.key ? <LoaderCircle className="animate-spin" /> : <MoreHorizontal />}
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => void sample.open(template)}>
                        <FileText /> Open sample
                      </DropdownMenuItem>
                      {!template.builtin && (
                        <>
                          <DropdownMenuItem onSelect={() => setEditing(template)}>
                            <Pencil /> Adjust style…
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem className="text-destructive focus:text-destructive" onSelect={() => setDeleting(template)}>
                            <Trash2 /> Delete
                          </DropdownMenuItem>
                        </>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
                {!selected && (
                  <Button size="sm" variant="outline" disabled={updateSettings.isPending} onClick={() => use(template)}>
                    Use this template
                  </Button>
                )}
              </Card>
            );
          })}
        </div>
      )}

      <TemplateEditor
        template={editing}
        onClose={() => setEditing(null)}
        onSaved={(saved) => {
          void refresh();
          if (lastImport?.id === saved.id) setLastImport(null);
        }}
      />
      <Modal
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={`Delete “${deleting?.name}”?`}
        description={
          deleting?.key === data?.selected ? "It's in use; new documents will go back to the Professional template." : "Documents already exported keep their look."
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => deleting && void remove(deleting)}>
              Delete template
            </Button>
          </>
        }
      />
    </Section>
  );
}

function ColorField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (value: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      <input id={id} type="color" className="h-8 w-10 cursor-pointer rounded border bg-transparent" value={`#${value}`} onChange={(e) => onChange(e.target.value.slice(1).toUpperCase())} />
      <Label htmlFor={id} className="font-normal">
        {label}
      </Label>
    </div>
  );
}

function NumberField({ id, label, value, min, max, step, onChange }: { id: string; label: string; value: number; min: number; max: number; step: number; onChange: (value: number) => void }) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">
        {label}
      </Label>
      <Input id={id} type="number" min={min} max={max} step={step} value={value} onChange={(e) => e.target.value !== "" && onChange(Math.min(max, Math.max(min, Number(e.target.value))))} />
    </div>
  );
}

function SwitchField({ id, label, checked, onChange }: { id: string; label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <div className="flex items-center gap-2">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <Label htmlFor={id} className="font-normal">
        {label}
      </Label>
    </div>
  );
}

/** Adjust an imported style, with a live preview. */
export function TemplateEditor({ template, onClose, onSaved }: { template: DocumentTemplate | null; onClose: () => void; onSaved: (template: DocumentTemplate) => void }) {
  const [name, setName] = useState("");
  const [style, setStyle] = useState<TemplateStyle | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (template) {
      setName(template.name);
      setStyle(template.style);
    }
  }, [template]);
  const set = <K extends keyof TemplateStyle>(key: K, value: TemplateStyle[K]) => setStyle((s) => (s ? { ...s, [key]: value } : s));

  const save = async () => {
    if (!template?.id || !style) return;
    setSaving(true);
    try {
      const saved = await api.templates.update(template.id, { name, style });
      onSaved(saved);
      toast.success(`Saved “${saved.name}”`);
      onClose();
    } catch (error) {
      toast.error("Not saved", { description: describeError(error) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={template !== null}
      onOpenChange={(open) => !open && onClose()}
      title="Adjust template style"
      description="Fix anything the copy didn't catch. The preview updates as you go."
      className="max-w-5xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={saving || !name.trim()} onClick={() => void save()}>
            {saving && <LoaderCircle className="animate-spin" />} Save template
          </Button>
        </>
      }
    >
      {style && (
        <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_auto]">
          <div className="space-y-4">
            <datalist id="template-fonts">
              {COMMON_FONTS.map((font) => (
                <option key={font} value={font} />
              ))}
            </datalist>
            <div className="space-y-1">
              <Label htmlFor="tpl-name">Template name</Label>
              <Input id="tpl-name" value={name} maxLength={90} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor="tpl-font" className="text-xs font-normal text-muted-foreground">
                  Body font
                </Label>
                <Input id="tpl-font" list="template-fonts" value={style.font} onChange={(e) => e.target.value && set("font", e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="tpl-heading-font" className="text-xs font-normal text-muted-foreground">
                  Name & heading font
                </Label>
                <Input id="tpl-heading-font" list="template-fonts" value={style.heading_font} onChange={(e) => e.target.value && set("heading_font", e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <NumberField id="tpl-size" label="Body size (pt)" value={style.size} min={7} max={14} step={0.5} onChange={(v) => set("size", v)} />
              <NumberField id="tpl-name-size" label="Name size (pt)" value={style.name_size} min={11} max={40} step={0.5} onChange={(v) => set("name_size", v)} />
              <NumberField id="tpl-heading-size" label="Heading size (pt)" value={style.heading_size} min={8} max={18} step={0.5} onChange={(v) => set("heading_size", v)} />
              <NumberField id="tpl-margin" label="Margins (in)" value={style.margin} min={0.3} max={1.5} step={0.05} onChange={(v) => set("margin", v)} />
              <NumberField id="tpl-space" label="Paragraph space (pt)" value={style.paragraph_space} min={0} max={12} step={0.5} onChange={(v) => set("paragraph_space", v)} />
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2">
              <ColorField id="tpl-name-color" label="Name color" value={style.name_color} onChange={(v) => set("name_color", v)} />
              <ColorField id="tpl-heading-color" label="Headings" value={style.heading_color} onChange={(v) => set("heading_color", v)} />
              <ColorField id="tpl-text-color" label="Body text" value={style.text_color} onChange={(v) => set("text_color", v)} />
              <ColorField id="tpl-muted-color" label="Contact & dates" value={style.muted_color} onChange={(v) => set("muted_color", v)} />
              <ColorField id="tpl-rule-color" label="Heading line" value={style.rule_color} onChange={(v) => set("rule_color", v)} />
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-2">
              <SwitchField id="tpl-name-bold" label="Bold name" checked={style.name_bold} onChange={(v) => set("name_bold", v)} />
              <SwitchField id="tpl-heading-bold" label="Bold headings" checked={style.heading_bold} onChange={(v) => set("heading_bold", v)} />
              <SwitchField id="tpl-heading-caps" label="Capital headings" checked={style.heading_caps} onChange={(v) => set("heading_caps", v)} />
              <SwitchField id="tpl-heading-rule" label="Line under headings" checked={style.heading_rule} onChange={(v) => set("heading_rule", v)} />
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm">Name position</span>
              <Segmented<TemplateStyle["name_align"]>
                ariaLabel="Name position"
                value={style.name_align}
                onValueChange={(v) => set("name_align", v)}
                options={[
                  { value: "left", label: "Left" },
                  { value: "center", label: "Centered" },
                ]}
              />
            </div>
          </div>
          <TemplateThumbnail style={style} scale={0.5} className="w-[360px]" />
        </div>
      )}
    </Modal>
  );
}
