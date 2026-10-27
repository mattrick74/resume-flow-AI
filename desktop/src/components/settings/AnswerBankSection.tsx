import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Row, Section } from "@/components/settings/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { AnswerBankEntry } from "@/lib/types";

const KEY = ["answer-bank"];

/**
 * v2.0: answers the Chrome extension fills into application forms without AI (requirements 6.1). These are for every
 * candidate; answers saved from the side panel for one candidate are listed too and override these for that candidate.
 */
export function AnswerBankSection() {
  const client = useQueryClient();
  const bank = useQuery({ queryKey: KEY, queryFn: () => api.answerBank.get() });
  const entries = bank.data?.entries ?? [];
  const global = new Map(entries.filter((e) => e.candidate_id === null).map((e) => [e.key, e]));
  const custom = entries.filter((e) => e.key.startsWith("q:") || e.candidate_id !== null);

  const refresh = () => void client.invalidateQueries({ queryKey: KEY });
  const save = async (key: string, answer: string) => {
    try {
      if (answer.trim()) await api.answerBank.save({ key, answer: answer.trim() });
      else if (global.get(key)) await api.answerBank.remove(global.get(key)!.id);
      refresh();
    } catch (error) {
      toast.error("Couldn't save the answer", { description: describeError(error) });
    }
  };
  const remove = async (entry: AnswerBankEntry) => {
    try {
      await api.answerBank.remove(entry.id);
      refresh();
    } catch (error) {
      toast.error("Couldn't delete the answer", { description: describeError(error) });
    }
  };

  return (
    <Section
      id="answer-bank"
      title="Application answers"
      description="The Chrome extension fills these into application forms, with no AI. Self-identification questions with no answer here are declined when the form offers that; otherwise they are left for you."
    >
      {(bank.data?.standard ?? []).map((item) => (
        <AnswerField key={item.key} id={`answer-${item.key}`} label={item.label} saved={global.get(item.key)?.answer ?? ""} onSave={(value) => save(item.key, value)} />
      ))}
      {custom.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">Saved from application forms</h3>
          {custom.map((entry) => (
            <div key={entry.id} className="flex items-start justify-between gap-3 rounded-md border p-2 text-sm">
              <div className="min-w-0">
                <div className="font-medium">{entry.question || entry.key}</div>
                <div className="text-muted-foreground">
                  {entry.answer}
                  {entry.candidate_id ? " · for one candidate" : ""}
                </div>
              </div>
              <Button variant="ghost" size="sm" aria-label={`Delete the answer to ${entry.question || entry.key}`} onClick={() => void remove(entry)}>
                <Trash2 />
              </Button>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function AnswerField({ id, label, saved, onSave }: { id: string; label: string; saved: string; onSave: (value: string) => void }) {
  const [value, setValue] = useState(saved);
  useEffect(() => setValue(saved), [saved]);
  return (
    <Row label={label} htmlFor={id}>
      <Input id={id} value={value} maxLength={5000} onChange={(e) => setValue(e.target.value)} onBlur={() => value !== saved && onSave(value)} />
    </Row>
  );
}
