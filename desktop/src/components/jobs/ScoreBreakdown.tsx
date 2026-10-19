import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { MatchDetails } from "@/lib/types";

type Components = NonNullable<MatchDetails["components"]>;

// Fixed slot order = fixed colors per component (never by rank).
const PARTS: { key: keyof Components; label: string; color: string }[] = [
  { key: "mandatory_skills", label: "Required skills", color: "var(--series-1)" },
  { key: "experience", label: "Relevant experience", color: "var(--series-2)" },
  { key: "seniority", label: "Seniority & years", color: "var(--series-3)" },
  { key: "preferred_skills", label: "Preferred skills", color: "var(--series-4)" },
  { key: "industry", label: "Industry", color: "var(--series-5)" },
  { key: "other", label: "Other fit", color: "var(--series-6)" },
];

/** Stacked bar of each weighted component's contribution to the 0–100 score, with a labeled legend. */
export function ScoreBreakdown({ components, compact = false }: { components: Components; compact?: boolean }) {
  const parts = PARTS.map((part) => {
    const component = components[part.key];
    return { ...part, points: component.score * component.weight, weight: component.weight, score: component.score };
  });
  const total = parts.reduce((sum, p) => sum + p.points, 0);
  return (
    <div>
      <div className="flex h-2.5 w-full gap-[2px]" role="img" aria-label={`Score ${total.toFixed(0)} of 100, by component`}>
        {parts
          .filter((p) => p.points > 0.05)
          .map((part, index, visible) => (
            <Tooltip key={part.key}>
              <TooltipTrigger asChild>
                <div
                  className={index === visible.length - 1 ? "h-full rounded-r-[4px]" : "h-full"}
                  style={{ flexBasis: `${part.points}%`, background: part.color }}
                />
              </TooltipTrigger>
              <TooltipContent>
                {part.label}: {part.points.toFixed(1)} of {part.weight} pts ({Math.round(part.score * 100)}%)
              </TooltipContent>
            </Tooltip>
          ))}
        {total < 99.5 && <div className="h-full flex-1 rounded-[2px] bg-muted" aria-hidden />}
      </div>
      {!compact && (
        <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          {parts.map((part) => (
            <li key={part.key} className="flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2 shrink-0 rounded-[2px]" style={{ background: part.color }} aria-hidden />
              <span className="truncate">{part.label}</span>
              <span className="ml-auto tabular-nums text-foreground">
                {part.points.toFixed(0)}/{part.weight}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
