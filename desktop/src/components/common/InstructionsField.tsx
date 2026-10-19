import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useSettings } from "@/lib/queries";

/** The operator's own notes for the AI writer for one application (Settings holds the default for all). */
export function InstructionsField({ value, onChange, id = "ai-instructions" }: { value: string; onChange: (value: string) => void; id?: string }) {
  const settings = useSettings();
  const hasKey = settings.data?.secrets_configured.openai_api_key ?? true;
  const defaults = settings.data?.settings.writing_instructions?.trim();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>Instructions for the AI (optional)</Label>
      <Textarea
        id={id}
        rows={4}
        maxLength={2000}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="e.g. Lead with the .NET and Azure work, keep the summary to two sentences, and mention the healthcare projects in the cover letter."
      />
      <p className="text-xs text-muted-foreground">
        {defaults ? "Your default instructions from Settings are added too. " : ""}
        The AI follows these for wording, emphasis and order, but can't add facts: anything the candidate's profile doesn't support is still removed, so
        add missing skills and projects to the profile first.{hasKey ? "" : " Add an OpenAI key in Settings: without one, documents are written offline and instructions aren't used."}
      </p>
    </div>
  );
}
