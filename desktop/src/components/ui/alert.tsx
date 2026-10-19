import { cva, type VariantProps } from "class-variance-authority";
import { CircleAlert, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const alertVariants = cva("flex flex-wrap gap-3 rounded-lg border px-4 py-3 text-sm", {
  variants: {
    variant: {
      info: "border-primary/20 bg-accent/60 text-accent-foreground",
      warning: "border-warning/30 bg-warning/10 text-foreground",
      destructive: "border-destructive/25 bg-destructive/10 text-foreground",
    },
  },
  defaultVariants: { variant: "info" },
});

const ICONS = { info: Info, warning: TriangleAlert, destructive: CircleAlert };
const ICON_COLOR = { info: "text-primary", warning: "text-warning", destructive: "text-destructive" };

function Alert({
  variant = "info",
  title,
  children,
  action,
  className,
}: VariantProps<typeof alertVariants> & { title?: ReactNode; children?: ReactNode; action?: ReactNode; className?: string }) {
  const key = variant ?? "info";
  const Icon = ICONS[key];
  return (
    <div role={key === "destructive" ? "alert" : "status"} className={cn(alertVariants({ variant }), className)}>
      <Icon className={cn("mt-0.5 size-4 shrink-0", ICON_COLOR[key])} aria-hidden />
      {/* The text keeps a readable width; in a narrow window the action wraps below it. */}
      <div className="min-w-56 flex-1 basis-0">
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={cn("text-muted-foreground", title && "mt-0.5")}>{children}</div>}
      </div>
      {action && <div className="ml-auto shrink-0 self-center">{action}</div>}
    </div>
  );
}

export { Alert };
