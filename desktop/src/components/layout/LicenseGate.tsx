import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, LoaderCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { describeError } from "@/components/common/ErrorState";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { LICENSE_EVENT, api } from "@/lib/api";
import type { LicenseStatus } from "@/lib/types";

const KEY = ["license"];

/**
 * v2.0: the backend works only with a valid license key. Without one (or once it expires) this screen asks for a key and
 * shows the PC's machine code to send to whoever issues keys. Shared by the desktop app and the extension's workspace.
 */
export function LicenseGate({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const license = useQuery({ queryKey: KEY, queryFn: () => api.license.get(), refetchInterval: 15 * 60_000 });

  // A request refused for the license while the app is open: check again, which shows this screen.
  useEffect(() => {
    const recheck = () => void client.invalidateQueries({ queryKey: KEY });
    window.addEventListener(LICENSE_EVENT, recheck);
    return () => window.removeEventListener(LICENSE_EVENT, recheck);
  }, [client]);

  if (license.isPending) {
    return (
      <div className="flex h-full items-center justify-center bg-background">
        <LoaderCircle className="size-5 animate-spin text-primary" aria-label="Checking the license" />
      </div>
    );
  }
  if (license.data && !license.data.valid) return <LicenseScreen status={license.data} />;
  const daysLeft = license.data?.required ? license.data.days_left : null;
  return (
    <>
      {daysLeft !== null && daysLeft !== undefined && daysLeft <= 2 && <ExpiryBanner status={license.data!} />}
      {children}
    </>
  );
}

function ExpiryBanner({ status }: { status: LicenseStatus }) {
  const [open, setOpen] = useState(false);
  if (open) return <LicenseScreen status={status} onClose={() => setOpen(false)} />;
  const when = status.expires_at ? new Date(status.expires_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "soon";
  return (
    <div role="status" className="flex items-center gap-3 border-b bg-warning/15 px-4 py-2 text-sm">
      <KeyRound className="size-4 shrink-0" aria-hidden />
      <span className="flex-1">Your ResumeFlow license ends {when}. Ask for a new key and enter it before then.</span>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Enter new key
      </Button>
    </div>
  );
}

function LicenseScreen({ status, onClose }: { status: LicenseStatus; onClose?: () => void }) {
  const client = useQueryClient();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activate = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.license.activate(key);
      client.setQueryData(KEY, result);
      await client.invalidateQueries();
      toast.success("License activated", { description: result.days_left !== null ? `Valid for ${Math.floor(result.days_left)} more days.` : undefined });
      onClose?.();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(status.machine_code);
      toast.success("Machine code copied");
    } catch {
      toast.error("Couldn't copy; select the code and copy it");
    }
  };

  return (
    <div className="flex h-full items-center justify-center bg-background p-6">
      <div className="w-full max-w-md space-y-4">
        <div className="mx-auto flex size-12 items-center justify-center rounded-xl border bg-card shadow-xs">
          <KeyRound className="size-5 text-primary" aria-hidden />
        </div>
        <div className="text-center">
          <h1 className="text-base font-semibold">{status.valid ? "Enter a new license key" : "ResumeFlow needs a license key"}</h1>
          <p className="mt-2 text-sm text-muted-foreground" role="status">
            {status.message ?? "Paste the new key you were given."}
          </p>
        </div>
        <div className="rounded-lg border bg-card p-3 text-sm">
          <div className="text-xs text-muted-foreground">This PC&apos;s machine code (send it with your request for a key)</div>
          <div className="mt-1 flex items-center gap-2">
            <code className="flex-1 font-mono text-base tracking-wide select-all">{status.machine_code}</code>
            <Button size="sm" variant="ghost" onClick={() => void copy()} aria-label="Copy the machine code">
              <Copy />
            </Button>
          </div>
        </div>
        <Textarea
          value={key}
          onChange={(e) => setKey(e.target.value)}
          rows={4}
          placeholder="RF1-…"
          aria-label="License key"
          aria-invalid={Boolean(error)}
          className="font-mono text-xs"
        />
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          {onClose && (
            <Button variant="ghost" onClick={onClose}>
              Not now
            </Button>
          )}
          <Button onClick={() => void activate()} disabled={busy || key.trim().length < 10}>
            {busy ? <LoaderCircle className="animate-spin" /> : <KeyRound />} Activate
          </Button>
        </div>
      </div>
    </div>
  );
}
