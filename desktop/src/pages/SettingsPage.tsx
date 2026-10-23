import { Eye, EyeOff, KeyRound, Monitor, Moon, ShieldCheck, Sun } from "lucide-react";
import { useEffect, useRef, useState, type ComponentProps } from "react";
import { toast } from "sonner";

import { useChangeTheme, useTheme } from "@/app/theme";
import { ErrorState, describeError } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { AiOptionsSection, BackupsPanel, DocumentsSection, MatchingWeightsSection, ModelPricesSection } from "@/components/settings/AdvancedSections";
import { Row, Section } from "@/components/settings/layout";
import { TemplatesSection } from "@/components/settings/TemplatesSection";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api";
import { useSecretMutations, useSettings, useUpdateSettings } from "@/lib/queries";
import type { AppSettings, SettingsResponse, ThemePreference, ValidationIssue } from "@/lib/types";

// Fields edited through the main form; the other settings sections save themselves.
type EditableKey =
  | "output_root"
  | "analysis_model"
  | "writing_model"
  | "daily_goal"
  | "worker_concurrency"
  | "backup_retention_count"
  | "daily_budget_usd"
  | "monthly_budget_usd"
  | "auto_fetch_job_urls";
type Draft = Record<Exclude<EditableKey, "auto_fetch_job_urls">, string> & { auto_fetch_job_urls: boolean };
type FieldErrors = Partial<Record<EditableKey, string>>;

const INTEGER_FIELDS = ["daily_goal", "worker_concurrency", "backup_retention_count"] as const;
const BUDGET_FIELDS = ["daily_budget_usd", "monthly_budget_usd"] as const;
const TEXT_FIELDS = ["output_root", "analysis_model", "writing_model"] as const;

function toDraft(settings: AppSettings): Draft {
  return {
    output_root: settings.output_root,
    analysis_model: settings.analysis_model,
    writing_model: settings.writing_model,
    daily_goal: String(settings.daily_goal),
    worker_concurrency: String(settings.worker_concurrency),
    backup_retention_count: String(settings.backup_retention_count),
    daily_budget_usd: settings.daily_budget_usd?.toString() ?? "",
    monthly_budget_usd: settings.monthly_budget_usd?.toString() ?? "",
    auto_fetch_job_urls: settings.auto_fetch_job_urls,
  };
}

/** Returns only changed fields, or client-side errors. */
export function buildPatch(draft: Draft, saved: AppSettings): { patch: Partial<AppSettings>; errors: FieldErrors } {
  const patch: Partial<AppSettings> = {};
  const errors: FieldErrors = {};

  for (const key of TEXT_FIELDS) {
    const value = draft[key].trim();
    if (!value) errors[key] = "Required";
    else if (value !== saved[key]) patch[key] = value;
  }
  for (const key of INTEGER_FIELDS) {
    const value = Number(draft[key]);
    if (draft[key].trim() === "" || !Number.isInteger(value)) errors[key] = "Enter a whole number";
    else if (value !== saved[key]) patch[key] = value;
  }
  for (const key of BUDGET_FIELDS) {
    const raw = draft[key].trim();
    const value = raw === "" ? null : Number(raw);
    if (value !== null && (Number.isNaN(value) || value < 0)) errors[key] = "Enter an amount of 0 or more, or leave empty";
    else if (value !== saved[key]) patch[key] = value;
  }
  if (draft.auto_fetch_job_urls !== saved.auto_fetch_job_urls) patch.auto_fetch_job_urls = draft.auto_fetch_job_urls;
  return { patch, errors };
}

function serverFieldErrors(error: unknown): FieldErrors {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return {};
  const errors: FieldErrors = {};
  for (const issue of error.details as ValidationIssue[]) {
    const field = [...issue.loc].reverse().find((part) => typeof part === "string") as EditableKey | undefined;
    if (field) errors[field] = issue.msg.replace(/^Value error, /, "");
  }
  return errors;
}

export function SettingsPage() {
  const settings = useSettings();
  return (
    <>
      <PageHeader title="Settings" description="Preferences are stored locally. Secrets go to Windows Credential Manager." />
      {settings.isPending ? (
        <Skeleton className="h-[640px]" />
      ) : settings.isError ? (
        <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
      ) : (
        <SettingsForm data={settings.data} />
      )}
    </>
  );
}

function SettingsForm({ data }: { data: SettingsResponse }) {
  const saved = data.settings;
  const [draft, setDraft] = useState<Draft>(() => toDraft(saved));
  const [errors, setErrors] = useState<FieldErrors>({});
  const update = useUpdateSettings();

  // Pick up changes saved elsewhere (e.g. the theme toggle) without clobbering
  // fields the operator has edited relative to the previous baseline.
  const baseline = useRef(saved);
  useEffect(() => {
    const previous = toDraft(baseline.current);
    baseline.current = saved;
    setDraft((current) => ({ ...toDraft(saved), ...dirtyFields(current, previous) }));
  }, [saved]);

  const dirty = Object.keys(dirtyFields(draft, toDraft(saved))).length > 0;

  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  };

  const save = () => {
    const result = buildPatch(draft, saved);
    if (Object.keys(result.errors).length) {
      setErrors(result.errors);
      return;
    }
    if (!Object.keys(result.patch).length) return;
    update.mutate(result.patch, {
      onSuccess: (response) => {
        setDraft(toDraft(response.settings));
        setErrors({});
        toast.success("Settings saved");
      },
      onError: (error) => {
        setErrors(serverFieldErrors(error));
        toast.error("Settings not saved", { description: describeError(error) });
      },
    });
  };

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty) save();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const field = (key: Exclude<EditableKey, "auto_fetch_job_urls">, props: ComponentProps<typeof Input> = {}) => (
    <Input
      id={key}
      value={draft[key]}
      onChange={(e) => set(key, e.target.value)}
      aria-invalid={errors[key] ? true : undefined}
      aria-describedby={errors[key] ? `${key}-error` : undefined}
      {...props}
    />
  );

  return (
    <form
      // Validation is shown inline (client checks + sidecar 422s), not as browser bubbles.
      noValidate
      className="space-y-4 pb-20"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <Section title="AI provider" description="OpenAI Responses API. Job descriptions and the selected candidate’s data are sent only when you analyze or generate.">
        <ApiKeyField configured={data.secrets_configured.openai_api_key ?? false} />
        <Row label="Analysis model" htmlFor="analysis_model" hint="Economical model for JD extraction and matching." error={errors.analysis_model}>
          {field("analysis_model", { spellCheck: false })}
        </Row>
        <Row label="Writing model" htmlFor="writing_model" hint="Stronger model for resume and cover letter drafting." error={errors.writing_model}>
          {field("writing_model", { spellCheck: false })}
        </Row>
      </Section>

      <AiOptionsSection settings={saved} />
      <MatchingWeightsSection settings={saved} />

      <Section title="Output" description="Where exported documents are written. Existing files are never overwritten.">
        <Row label="Output folder" htmlFor="output_root" hint="Every export is saved as YYYY-MM-DD / daily number and profile name, for example 001 Avery Quinlan." error={errors.output_root}>
          {field("output_root", { spellCheck: false })}
        </Row>
      </Section>

      <TemplatesSection />

      <DocumentsSection settings={saved} />

      <Section title="Throughput" description="Daily target and how many background jobs run at once.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Row label="Daily goal" htmlFor="daily_goal" hint="Applications per day (1–1000)." error={errors.daily_goal}>
            {field("daily_goal", { type: "number", min: 1, max: 1000, inputMode: "numeric" })}
          </Row>
          <Row label="Concurrent workers" htmlFor="worker_concurrency" hint="1–8. Start with 2–3 to respect rate limits." error={errors.worker_concurrency}>
            {field("worker_concurrency", { type: "number", min: 1, max: 8, inputMode: "numeric" })}
          </Row>
        </div>
      </Section>

      <Section title="Budgets" description="Spend limits in US dollars. Leave empty for no limit.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Row label="Daily budget" htmlFor="daily_budget_usd" error={errors.daily_budget_usd}>
            {field("daily_budget_usd", { type: "number", min: 0, step: "0.01", placeholder: "No limit", inputMode: "decimal" })}
          </Row>
          <Row label="Monthly budget" htmlFor="monthly_budget_usd" error={errors.monthly_budget_usd}>
            {field("monthly_budget_usd", { type: "number", min: 0, step: "0.01", placeholder: "No limit", inputMode: "decimal" })}
          </Row>
        </div>
      </Section>

      <ModelPricesSection settings={saved} />

      <Section title="Privacy" description="When you provide a job URL, it is kept as a source record and is not visited unless you allow it.">
        <div className="flex items-start justify-between gap-6">
          <div>
            <Label htmlFor="auto_fetch_job_urls">Fetch job pages automatically</Label>
            <p className="mt-1 text-xs text-muted-foreground">Off by default. When on, the posting URL may be downloaded to fill in missing details.</p>
          </div>
          <Switch
            id="auto_fetch_job_urls"
            checked={draft.auto_fetch_job_urls}
            onCheckedChange={(checked) => set("auto_fetch_job_urls", checked)}
          />
        </div>
      </Section>

      <Section title="Backups" description="Database snapshots kept in your ResumeFlowAI data folder.">
        <Row label="Snapshots to keep" htmlFor="backup_retention_count" hint="1–365." error={errors.backup_retention_count}>
          {field("backup_retention_count", { type: "number", min: 1, max: 365, inputMode: "numeric", className: "sm:w-40" })}
        </Row>
        <BackupsPanel />
      </Section>

      <AppearanceSection />

      <Section title="Storage" description="Local data locations. Read-only.">
        <Row label="Data folder" htmlFor="data_dir">
          <Input id="data_dir" value={data.paths.data_dir} readOnly />
        </Row>
        <Row label="Database" htmlFor="database">
          <Input id="database" value={data.paths.database} readOnly />
        </Row>
      </Section>

      {dirty && (
        <div className="sticky bottom-4 z-10 flex items-center gap-3 rounded-xl border bg-popover px-4 py-3 shadow-lg animate-in fade-in-0 slide-in-from-bottom-2">
          <span className="flex-1 text-sm">You have unsaved changes</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setDraft(toDraft(saved));
              setErrors({});
            }}
          >
            Discard
          </Button>
          <Button type="submit" size="sm" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save changes"} <Kbd className="border-primary-foreground/20 bg-primary-foreground/10 text-primary-foreground">Ctrl S</Kbd>
          </Button>
        </div>
      )}
    </form>
  );
}

function dirtyFields(draft: Draft, baseline: Draft): Partial<Draft> {
  const changed: Partial<Draft> = {};
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    if (draft[key] !== baseline[key]) (changed as Record<string, unknown>)[key] = draft[key];
  }
  return changed;
}

function AppearanceSection() {
  const { preference } = useTheme();
  const changeTheme = useChangeTheme();
  return (
    <Section title="Appearance" description="Applies immediately.">
      <Segmented<ThemePreference>
        ariaLabel="Theme"
        value={preference}
        onValueChange={changeTheme}
        options={[
          { value: "system", label: "System", icon: <Monitor /> },
          { value: "light", label: "Light", icon: <Sun /> },
          { value: "dark", label: "Dark", icon: <Moon /> },
        ]}
      />
    </Section>
  );
}

function ApiKeyField({ configured }: { configured: boolean }) {
  const { store, remove } = useSecretMutations("openai_api_key");
  const [editing, setEditing] = useState(!configured);
  const [value, setValue] = useState("");
  const [visible, setVisible] = useState(false);

  useEffect(() => setEditing(!configured), [configured]);

  const saveKey = () =>
    store.mutate(value.trim(), {
      onSuccess: () => {
        setValue("");
        setVisible(false);
        toast.success("API key saved to Windows Credential Manager");
      },
      onError: (error) => toast.error("API key not saved", { description: describeError(error) }),
    });

  const removeKey = () =>
    remove.mutate(undefined, {
      onSuccess: () => toast.success("API key removed"),
      onError: (error) => toast.error("API key not removed", { description: describeError(error) }),
    });

  return (
    <div className="rounded-lg border bg-muted/40 p-4">
      <div className="mb-3 flex items-center gap-2">
        <KeyRound className="size-4 text-muted-foreground" aria-hidden />
        <span className="text-sm font-medium">OpenAI API key</span>
        {configured ? (
          <Badge variant="success" className="ml-auto">
            <ShieldCheck /> Stored securely
          </Badge>
        ) : (
          <Badge variant="outline" className="ml-auto">
            Not set
          </Badge>
        )}
      </div>
      {editing ? (
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Input
              type={visible ? "text" : "password"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="sk-…"
              autoComplete="off"
              spellCheck={false}
              aria-label="OpenAI API key"
              className="pr-9"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (value.trim().length >= 8) saveKey();
                }
              }}
            />
            <button
              type="button"
              onClick={() => setVisible((v) => !v)}
              aria-label={visible ? "Hide key" : "Show key"}
              className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
          <Button type="button" onClick={saveKey} disabled={value.trim().length < 8 || store.isPending}>
            {store.isPending ? "Saving…" : "Save key"}
          </Button>
          {configured && (
            <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <span className="flex-1 font-mono text-sm tracking-widest text-muted-foreground">••••••••••••••••</span>
          <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
            Replace
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={removeKey} disabled={remove.isPending} className="text-destructive hover:bg-destructive/10 hover:text-destructive">
            Remove
          </Button>
        </div>
      )}
      <p className="mt-3 text-xs text-muted-foreground">
        Kept in Windows Credential Manager — never in the database, logs or app files.
      </p>
    </div>
  );
}
