import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Short id for client-created profile items (matches the backend's 10-hex-char ids). */
export function newId() {
  return crypto.randomUUID().replace(/-/g, "").slice(0, 10);
}

export function formatDateRange(start: string | null, end: string | null) {
  const fmt = (value: string | null) => {
    if (!value) return "";
    if (value.toLowerCase() === "present") return "Present";
    const match = /^(\d{4})-(\d{2})$/.exec(value);
    if (!match) return value;
    return new Date(Number(match[1]), Number(match[2]) - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
  };
  const left = fmt(start);
  const right = fmt(end);
  return left && right ? `${left} – ${right}` : left || right;
}

export function formatDate(iso: string) {
  return new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatCurrency(usd: number) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(usd);
}

export function formatDuration(ms: number | null) {
  if (ms === null) return "—";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatRelativeTime(iso: string, now = Date.now()) {
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return rtf.format(seconds, "second");
}
