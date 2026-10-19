import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, CircleX, DatabaseBackup, History, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Row, Section } from "@/components/settings/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { NativeSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { queryKeys, useBackups, usePdfConverter, useUpdateSettings } from "@/lib/queries";
import type { AppSettings, BackupInfo, MatchWeights } from "@/lib/types";
import { cn, formatRelativeTime } from "@/lib/utils";

const DEFAULT_PRICES: AppSettings["model_prices"] = {
  // Mirrors the backend's built-in prices (OpenAI pricing page, October 2026); any can be overridden here.
  "gpt-6-astra": { input: 10, output: 50 },
  "gpt-6.1-sol": { input: 2, output: 10 },
  "gpt-6-luna": { input: 0.1, output: 0.5 },
  "gpt-5.4": { input: 2.5, output: 15 },
  "gpt-5.4-mini": { input: 0.75, output: 4.5 },
  "gpt-5": { input: 1.25, output: 10 },
  "gpt-5-mini": { input: 0.25, output: 2 },
  "gpt-5-nano": { input: 0.05, output: 0.4 },
};

function useSave() {
  const update = useUpdateSettings();
  return (changes: Partial<AppSettings>, message = "Saved") =>
    update.mutateAsync(changes).then(
      () => toast.success(message),
      (error) => toast.error("Not saved", { description: describeError(error) }),
    );
}

export function AiOptionsSection({ settings }: { settings: AppSettings }) {
  const save = useSave();
  return (
    <Section title="AI assistance" description="Where AI is used. Without an API key, everything falls back to offline, deterministic processing.">
      {(
        [
          ["ai_extract_on_import", "Extract resumes with AI", "Structured extraction of roles, bullets and skills from any resume layout. Check the result against the original resume."],
          ["semantic_matching", "Semantic skill matching", "For the top candidates, asks AI whether the candidate's experience demonstrates a required skill under a different name. Only accepted when it cites real evidence."],
        ] as const
      ).map(([key, label, hint]) => (
        <div key={key} className="flex items-start justify-between gap-6">
          <div>
            <Label htmlFor={key}>{label}</Label>
            <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
          </div>
          <Switch id={key} checked={settings[key]} onCheckedChange={(value) => void save({ [key]: value })} />
        </div>
      ))}
    </Section>
  );
}

const WEIGHT_LABELS: Record<keyof MatchWeights, string> = {
  mandatory_skills: "Required skills",
  experience: "Relevant experience",
  seniority: "Seniority & years",
  preferred_skills: "Preferred skills",
  industry: "Industry / domain",
  other: "Other fit",
};

export function MatchingWeightsSection({ settings }: { settings: AppSettings }) {
  const save = useSave();
  const [weights, setWeights] = useState<MatchWeights>(settings.match_weights);
  const savedKey = JSON.stringify(settings.match_weights);
  useEffect(() => setWeights(JSON.parse(savedKey)), [savedKey]);
  const total = Object.values(weights).reduce((sum, v) => sum + (Number(v) || 0), 0);
  const dirty = JSON.stringify(weights) !== savedKey;
  return (
    <Section title="Matching weights" description="How the 0–100 match score is built. Heuristics, not hiring probabilities. Hard requirements are separate pass/fail gates.">
      <div className="grid gap-3 sm:grid-cols-3">
        {(Object.keys(WEIGHT_LABELS) as (keyof MatchWeights)[]).map((key) => (
          <div key={key} className="space-y-1.5">
            <Label htmlFor={`w-${key}`}>{WEIGHT_LABELS[key]}</Label>
            <Input
              id={`w-${key}`}
              type="number"
              min={0}
              max={100}
              value={weights[key]}
              onChange={(e) => setWeights((w) => ({ ...w, [key]: Number(e.target.value) }))}
            />
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <span className={cn("text-sm tabular-nums", Math.abs(total - 100) > 0.01 ? "text-destructive" : "text-muted-foreground")}>Total {total} / 100</span>
        <Button type="button" size="sm" variant="outline" disabled={!dirty || Math.abs(total - 100) > 0.01} onClick={() => void save({ match_weights: weights }, "Weights saved — re-run matching to apply")}>
          Save weights
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setWeights({ mandatory_skills: 35, experience: 25, seniority: 15, preferred_skills: 10, industry: 10, other: 5 })}
        >
          Reset to defaults
        </Button>
      </div>
    </Section>
  );
}

export function DocumentsSection({ settings }: { settings: AppSettings }) {
  const save = useSave();
  const converter = usePdfConverter();
  const client = useQueryClient();
  const [path, setPath] = useState(settings.libreoffice_path ?? "");
  const [instructions, setInstructions] = useState(settings.writing_instructions ?? "");
  useEffect(() => setInstructions(settings.writing_instructions ?? ""), [settings.writing_instructions]);
  const status = converter.data;
  return (
    <Section title="Documents" description="Layout is fixed by templates; AI never controls formatting.">
      <Row
        label="After generating"
        htmlFor="export-mode"
        hint={
          settings.export_mode === "review"
            ? "Every application waits in the Review Queue until you approve it."
            : settings.export_mode === "auto"
              ? "Generate saves the resume and cover letter files straight away. If the checks find an error (e.g. a number that isn't in the candidate's resume), that application waits in the Review Queue instead."
              : "Files are always saved straight away, even when the checks found errors. Check the Review Queue for flagged applications."
        }
      >
        <NativeSelect id="export-mode" className="sm:w-80" value={settings.export_mode} onChange={(e) => void save({ export_mode: e.target.value as AppSettings["export_mode"] })}>
          <option value="auto">Save files automatically (hold any with errors)</option>
          <option value="always">Always save files automatically</option>
          <option value="review">Wait for my approval</option>
        </NativeSelect>
      </Row>
      <div className="flex items-start justify-between gap-6">
        <div>
          <Label htmlFor="skip-known-urls">Skip job URLs already in history</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            A job whose posting URL you added before is skipped instead of being saved and generated again. If the earlier copy was only saved for later, Save &amp; generate generates that one.
          </p>
        </div>
        <Switch id="skip-known-urls" checked={settings.skip_known_urls} onCheckedChange={(value) => void save({ skip_known_urls: value })} />
      </div>
      <div className="flex items-start justify-between gap-6">
        <div>
          <Label htmlFor="require-verification">Require verification of candidate details</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            {settings.require_verification
              ? "Only details you tick as verified, after completing each candidate's review, are used for matching and resumes."
              : "Off: everything in a candidate's profile (experience, skills you add, education, certifications) is used as entered, with no ticks or review step. Skills you mark “Don't use” are left out."}
          </p>
        </div>
        <Switch id="require-verification" checked={settings.require_verification} onCheckedChange={(value) => void save({ require_verification: value })} />
      </div>
      <div className="flex items-start justify-between gap-6">
        <div>
          <Label htmlFor="gap-confirmation">Ask about missing job skills before generating</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            {settings.gap_confirmation === "ask"
              ? "Background generation waits when a job asks for skills the top candidate's profile doesn't show. Answer them once (Jobs → Answer missing skills) and every waiting job continues. Other jobs aren't held up."
              : "Generation runs straight through using only what the profile already shows. You can still answer missing skills on any job or application."}
          </p>
        </div>
        <Switch id="gap-confirmation" checked={settings.gap_confirmation === "ask"} onCheckedChange={(value) => void save({ gap_confirmation: value ? "ask" : "off" })} />
      </div>
      <div className="flex items-start justify-between gap-6">
        <div>
          <Label htmlFor="auto-approve-bullets">Add bullets from my descriptions without approval</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            {settings.auto_approve_bullets
              ? "When you say what was done with a skill at an employer, the bullet is added at once. If an AI draft says more than you wrote, your own wording is used instead."
              : "Bullets drafted from your descriptions wait on the candidate's Evidence tab until you approve them."}
          </p>
        </div>
        <Switch id="auto-approve-bullets" checked={settings.auto_approve_bullets} onCheckedChange={(value) => void save({ auto_approve_bullets: value })} />
      </div>
      <div className="flex items-start justify-between gap-6">
        <div>
          <Label htmlFor="hard-requirement-gates">Check hard requirements</Label>
          <p className="mt-1 text-xs text-muted-foreground">
            {settings.hard_requirement_gates
              ? "Work authorization, security clearance, licenses, location and work arrangement are pass/fail gates: a candidate who fails or can't be checked isn't ranked, so no resume is generated for them."
              : "Off: these never block or rank candidates. Every ready candidate is ranked by score and resumes are generated; check those conditions yourself before applying."}
          </p>
        </div>
        <Switch id="hard-requirement-gates" checked={settings.hard_requirement_gates} onCheckedChange={(value) => void save({ hard_requirement_gates: value })} />
      </div>
      <Row label="Bullets per role" htmlFor="bullet-density" hint="How many bullets each role gets on a tailored resume (recent roles / older roles), when the profile has that many.">
        <NativeSelect id="bullet-density" className="sm:w-80" value={settings.bullet_density} onChange={(e) => void save({ bullet_density: e.target.value as AppSettings["bullet_density"] })}>
          <option value="concise">Concise (2–4 / 1–3)</option>
          <option value="standard">Standard (3–6 / 2–4)</option>
          <option value="detailed">Detailed (5–8 / 3–5)</option>
        </NativeSelect>
      </Row>
      <Row
        label="Instructions for the AI writer"
        htmlFor="writing-instructions"
        hint="Followed for every resume and cover letter, on top of anything you add for one application. Wording, emphasis and order only: facts the candidate's evidence doesn't support are still removed."
      >
        <div className="space-y-2">
          <Textarea
            id="writing-instructions"
            rows={4}
            maxLength={2000}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="e.g. Use US spelling. Keep bullets under 25 words. Put the most relevant skills first and avoid buzzwords."
          />
          {instructions !== (settings.writing_instructions ?? "") && (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => void save({ writing_instructions: instructions.trim() }, "Instructions saved")}>
                Save instructions
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setInstructions(settings.writing_instructions ?? "")}>
                Discard
              </Button>
            </div>
          )}
        </div>
      </Row>
      <Row
        label="Files to save"
        htmlFor="output-formats"
        hint="PDF only: the Word file is just a step towards the PDF and isn't kept, unless the PDF can't be made (then you get the Word file instead)."
      >
        <NativeSelect id="output-formats" className="sm:w-80" value={settings.output_formats} onChange={(e) => void save({ output_formats: e.target.value as AppSettings["output_formats"] })}>
          <option value="pdf">PDF only</option>
          <option value="pdf_docx">PDF and Word (.docx)</option>
        </NativeSelect>
      </Row>
      <Row label="PDF converter" htmlFor="pdf-converter" hint="If conversion fails, the Word file is saved and you can retry the PDF.">
        <NativeSelect
          id="pdf-converter"
          className="sm:w-64"
          value={settings.pdf_converter}
          onChange={(e) =>
            void save({ pdf_converter: e.target.value as AppSettings["pdf_converter"] }).then(() => client.invalidateQueries({ queryKey: queryKeys.pdfConverter }))
          }
        >
          <option value="auto">Automatic (LibreOffice, then Word)</option>
          <option value="libreoffice">LibreOffice only</option>
          <option value="word">Microsoft Word only</option>
          <option value="none">Don't create PDFs</option>
        </NativeSelect>
      </Row>
      {status && (
        <ul className="space-y-1 text-sm">
          <li className="flex items-center gap-2">
            {status.libreoffice_path ? <CircleCheck className="size-4 text-success" /> : <CircleX className="size-4 text-muted-foreground" />}
            LibreOffice {status.libreoffice_path ? <code className="text-xs text-muted-foreground">{status.libreoffice_path}</code> : <span className="text-muted-foreground">not found</span>}
          </li>
          <li className="flex items-center gap-2">
            {status.word_available ? <CircleCheck className="size-4 text-success" /> : <CircleX className="size-4 text-muted-foreground" />}
            Microsoft Word {status.word_available ? "installed" : <span className="text-muted-foreground">not found</span>}
          </li>
          <li className={cn("text-xs", status.active ? "text-muted-foreground" : "text-destructive")}>
            {status.active ? `PDFs will be created with ${status.active === "word" ? "Microsoft Word" : "LibreOffice"}.` : "No converter available: PDFs will fail until one is installed."}
          </li>
        </ul>
      )}
      <Row label="LibreOffice location (optional)" htmlFor="lo-path" hint="Only needed if LibreOffice is installed somewhere unusual.">
        <Input
          id="lo-path"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onBlur={() => path !== (settings.libreoffice_path ?? "") && void save({ libreoffice_path: path || null }).then(() => client.invalidateQueries({ queryKey: queryKeys.pdfConverter }))}
          placeholder="C:\Program Files\LibreOffice\program\soffice.exe"
          spellCheck={false}
        />
      </Row>
    </Section>
  );
}

export function ModelPricesSection({ settings }: { settings: AppSettings }) {
  const save = useSave();
  const merged = { ...DEFAULT_PRICES, ...settings.model_prices };
  for (const model of [settings.analysis_model, settings.writing_model]) if (!merged[model]) merged[model] = { input: 0, output: 0 };
  const [prices, setPrices] = useState(merged);
  const [newModel, setNewModel] = useState("");
  const savedKey = JSON.stringify(merged);
  useEffect(() => setPrices(JSON.parse(savedKey)), [savedKey]);
  const dirty = JSON.stringify(prices) !== savedKey;
  return (
    <Section title="Model prices" description="USD per 1M tokens, used to estimate cost and enforce budgets. Check current prices on openai.com/pricing.">
      <div className="rounded-lg border">
        <div className="grid grid-cols-[1fr_120px_120px_36px] gap-2 border-b px-3 py-2 text-xs font-medium text-muted-foreground">
          <span>Model</span>
          <span>Input</span>
          <span>Output</span>
          <span />
        </div>
        {Object.entries(prices).map(([model, price]) => (
          <div key={model} className="grid grid-cols-[1fr_120px_120px_36px] items-center gap-2 px-3 py-1.5">
            <span className="truncate text-sm">
              {model}
              {(model === settings.analysis_model || model === settings.writing_model) && <span className="ml-1.5 text-xs text-muted-foreground">in use</span>}
            </span>
            {(["input", "output"] as const).map((side) => (
              <Input
                key={side}
                type="number"
                min={0}
                step="0.01"
                className="h-8"
                value={price[side]}
                onChange={(e) => setPrices((p) => ({ ...p, [model]: { ...p[model], [side]: Number(e.target.value) } }))}
                aria-label={`${model} ${side} price`}
              />
            ))}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={model in DEFAULT_PRICES}
              onClick={() => setPrices(({ [model]: _removed, ...rest }) => rest)}
              aria-label={`Remove ${model}`}
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Input value={newModel} onChange={(e) => setNewModel(e.target.value)} placeholder="Add model name" className="w-48" aria-label="New model name" />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!newModel.trim() || newModel.trim() in prices}
          onClick={() => {
            setPrices((p) => ({ ...p, [newModel.trim()]: { input: 0, output: 0 } }));
            setNewModel("");
          }}
        >
          <Plus /> Add
        </Button>
        <Button type="button" size="sm" variant="outline" className="ml-auto" disabled={!dirty} onClick={() => void save({ model_prices: prices }, "Prices saved")}>
          Save prices
        </Button>
      </div>
    </Section>
  );
}

export function BackupsPanel() {
  const backups = useBackups();
  const client = useQueryClient();
  const [restoring, setRestoring] = useState<BackupInfo | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const backup = await api.backups.create();
      toast.success("Backup created", { description: backup.name });
      void client.invalidateQueries({ queryKey: queryKeys.backups });
    } catch (error) {
      toast.error("Backup failed", { description: describeError(error) });
    } finally {
      setBusy(false);
    }
  };

  const restore = async (backup: BackupInfo) => {
    setBusy(true);
    try {
      const result = await api.backups.restore(backup.name);
      toast.success("Database restored", { description: `A safety copy was saved as ${result.safety_backup}` });
      await client.invalidateQueries();
    } catch (error) {
      toast.error("Restore failed", { description: describeError(error) });
    } finally {
      setBusy(false);
      setRestoring(null);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" variant="outline" onClick={() => void create()} disabled={busy}>
          <DatabaseBackup /> Back up now
        </Button>
        <span className="text-xs text-muted-foreground">A backup is also made automatically once a day at startup.</span>
      </div>
      <ul className="divide-y rounded-lg border">
        {backups.data?.length === 0 && <li className="px-3 py-3 text-sm text-muted-foreground">No backups yet.</li>}
        {backups.data?.map((backup) => (
          <li key={backup.name} className="flex items-center gap-3 px-3 py-2 text-sm">
            <History className="size-4 text-muted-foreground" aria-hidden />
            <span className="min-w-0 flex-1 truncate">
              {new Date(backup.created_at).toLocaleString()}
              <span className="ml-2 text-xs text-muted-foreground">
                {(backup.size_bytes / 1024).toFixed(0)} KB · {formatRelativeTime(backup.created_at)}
                {backup.name.includes("prerestore") && " · safety copy"}
              </span>
            </span>
            <Button type="button" size="sm" variant="ghost" onClick={() => setRestoring(backup)} disabled={busy}>
              Restore
            </Button>
          </li>
        ))}
      </ul>
      <Modal
        open={restoring !== null}
        onOpenChange={(open) => !open && setRestoring(null)}
        title="Restore this backup?"
        description={`Everything in the database is replaced with the backup from ${restoring ? new Date(restoring.created_at).toLocaleString() : ""}. A safety copy of the current database is made first. Exported files on disk aren't touched.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRestoring(null)}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={busy} onClick={() => restoring && void restore(restoring)}>
              Restore backup
            </Button>
          </>
        }
      />
    </div>
  );
}
