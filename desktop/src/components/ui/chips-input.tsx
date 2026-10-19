import { X } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

/** Editable list of short values (skills, credentials). Enter or comma adds; Backspace removes the last. */
export function ChipsInput({
  values,
  onChange,
  placeholder,
  ariaLabel,
  className,
}: {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  ariaLabel: string;
  className?: string;
}) {
  const [text, setText] = useState("");
  const add = (raw: string) => {
    const items = raw
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean)
      .filter((v) => !values.some((existing) => existing.toLowerCase() === v.toLowerCase()));
    if (items.length) onChange([...values, ...items]);
    setText("");
  };
  return (
    <div
      className={cn(
        "flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-input bg-card px-2 py-1.5 shadow-xs focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/40",
        className,
      )}
    >
      {values.map((value) => (
        <span key={value} className="inline-flex items-center gap-1 rounded-md bg-secondary px-2 py-0.5 text-xs font-medium text-secondary-foreground">
          {value}
          <button
            type="button"
            onClick={() => onChange(values.filter((v) => v !== value))}
            className="rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Remove ${value}`}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <input
        value={text}
        onChange={(e) => (e.target.value.endsWith(",") ? add(e.target.value) : setText(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(text);
          } else if (e.key === "Backspace" && !text && values.length) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={() => text && add(text)}
        placeholder={values.length ? undefined : placeholder}
        aria-label={ariaLabel}
        className="min-w-24 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}
