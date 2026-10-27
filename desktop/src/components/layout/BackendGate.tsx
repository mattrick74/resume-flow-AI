import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RefreshCw, RotateCcw, ServerCrash } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Button } from "@/components/ui/button";
import { isTauri, restartBackend } from "@/lib/backend";
import { useHealth } from "@/lib/queries";

const STARTUP_GRACE_MS = 15_000;

function useRestartEngine() {
  const client = useQueryClient();
  const [restarting, setRestarting] = useState(false);
  const restart = async () => {
    setRestarting(true);
    try {
      await restartBackend();
      await client.invalidateQueries();
    } catch (error) {
      toast.error("Couldn't restart the engine", { description: describeError(error) });
    } finally {
      setRestarting(false);
    }
  };
  return { restart, restarting, available: isTauri() };
}

/**
 * Holds the UI on a startup screen until the sidecar answers /health once.
 * After that, outages are reported by the in-app banner instead.
 */
export function BackendGate({ children }: { children: ReactNode }) {
  const health = useHealth();
  const engine = useRestartEngine();
  const everReady = useRef(false);
  const [graceExpired, setGraceExpired] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setGraceExpired(true), STARTUP_GRACE_MS);
    return () => clearTimeout(timer);
  }, []);

  if (health.isSuccess) everReady.current = true;
  if (everReady.current) return <>{children}</>;

  const failed = graceExpired && health.isError;
  return (
    <div className="flex h-full items-center justify-center bg-background p-6">
      <div className="w-full max-w-sm text-center">
        <div className="mx-auto mb-5 flex size-12 items-center justify-center rounded-xl border bg-card shadow-xs">
          {failed ? (
            <ServerCrash className="size-5 text-destructive" aria-hidden />
          ) : (
            <LoaderCircle className="size-5 animate-spin text-primary" aria-hidden />
          )}
        </div>
        <h1 className="text-base font-semibold">{failed ? "The ResumeFlow engine didn’t start" : "Starting ResumeFlow engine…"}</h1>
        <p className="mt-2 text-sm text-muted-foreground" role="status">
          {failed
            ? "The local service that stores your data (shared with the Chrome extension) isn’t responding. Restart it, or retry. Details are in the backend log in your ResumeFlowAI data folder."
            : "Opening the local database and applying any updates."}
        </p>
        {failed && (
          <div className="mt-5 flex justify-center gap-2">
            {engine.available && (
              <Button onClick={() => void engine.restart()} disabled={engine.restarting}>
                <RotateCcw /> Restart engine
              </Button>
            )}
            <Button variant="outline" onClick={() => void health.refetch()}>
              <RefreshCw /> Retry
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export function EngineOfflineBanner() {
  const health = useHealth();
  const engine = useRestartEngine();
  if (!health.isError) return null;
  return (
    <div role="alert" className="flex items-center gap-2 border-b border-destructive/20 bg-destructive/10 px-6 py-2 text-sm text-destructive">
      <ServerCrash className="size-4" aria-hidden />
      <span className="flex-1">Lost connection to the ResumeFlow engine. Retrying automatically…</span>
      {engine.available && (
        <Button variant="ghost" size="sm" disabled={engine.restarting} onClick={() => void engine.restart()} className="text-destructive hover:bg-destructive/10">
          <RotateCcw /> Restart engine
        </Button>
      )}
      <Button variant="ghost" size="sm" onClick={() => void health.refetch()} className="text-destructive hover:bg-destructive/10">
        Retry now
      </Button>
    </div>
  );
}
