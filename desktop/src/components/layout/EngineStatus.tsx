import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useHealth } from "@/lib/queries";
import { cn } from "@/lib/utils";

export function EngineStatus({ compact = false }: { compact?: boolean }) {
  const health = useHealth();
  const state = health.isSuccess ? (health.data.status === "ok" ? "ready" : "degraded") : health.isError ? "offline" : "starting";
  const label = {
    ready: "Engine ready",
    degraded: "Schema out of date",
    offline: "Engine offline",
    starting: "Starting engine",
  }[state];
  const dot = {
    ready: "bg-success",
    degraded: "bg-warning",
    offline: "bg-destructive",
    starting: "bg-muted-foreground animate-pulse",
  }[state];

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          role="status"
          aria-label={label}
          className="flex h-8 items-center gap-2 rounded-md px-2 text-xs font-medium text-muted-foreground"
        >
          <span className={cn("size-2 rounded-full", dot)} aria-hidden />
          {!compact && <span>{label}</span>}
        </div>
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {health.data ? `Sidecar v${health.data.version} · schema ${health.data.schema_revision ?? "none"}` : label}
      </TooltipContent>
    </Tooltip>
  );
}
