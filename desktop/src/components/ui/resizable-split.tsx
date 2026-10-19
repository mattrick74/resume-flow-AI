import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Two panes with a draggable, keyboard-accessible divider (PRD §3 "resizable split views").
 * The split ratio is remembered per ``storageKey``.
 */
export function ResizableSplit({
  left,
  right,
  storageKey,
  initial = 0.5,
  min = 0.25,
  className,
}: {
  left: ReactNode;
  right: ReactNode;
  storageKey: string;
  initial?: number;
  min?: number;
  className?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [ratio, setRatio] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(`split.${storageKey}`));
      return stored > 0 && stored < 1 ? stored : initial;
    } catch {
      return initial;
    }
  });
  const dragging = useRef(false);
  const clamp = useCallback((value: number) => Math.min(1 - min, Math.max(min, value)), [min]);

  useEffect(() => {
    try {
      localStorage.setItem(`split.${storageKey}`, String(ratio));
    } catch {
      // preference only
    }
  }, [ratio, storageKey]);

  useEffect(() => {
    function onMove(event: PointerEvent) {
      if (!dragging.current || !container.current) return;
      const rect = container.current.getBoundingClientRect();
      setRatio(clamp((event.clientX - rect.left) / rect.width));
    }
    function onUp() {
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [clamp]);

  return (
    <div ref={container} className={cn("flex min-h-0 w-full", className)}>
      <div className="min-w-0 overflow-hidden" style={{ flexBasis: `${ratio * 100}%` }}>
        {left}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-valuemin={Math.round(min * 100)}
        aria-valuemax={Math.round((1 - min) * 100)}
        aria-valuenow={Math.round(ratio * 100)}
        aria-label="Resize panels"
        tabIndex={0}
        onPointerDown={(event) => {
          event.preventDefault();
          dragging.current = true;
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }}
        onDoubleClick={() => setRatio(initial)}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") setRatio((r) => clamp(r - 0.03));
          if (event.key === "ArrowRight") setRatio((r) => clamp(r + 0.03));
        }}
        className="group relative mx-2 w-1.5 shrink-0 cursor-col-resize rounded-full outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border transition-colors group-hover:bg-primary/50" />
      </div>
      <div className="min-w-0 flex-1 overflow-hidden">{right}</div>
    </div>
  );
}
