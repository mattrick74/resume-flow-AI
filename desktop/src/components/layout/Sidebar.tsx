import { FileText, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Link, useLocation } from "react-router";

import { findNavItem, NAV_GROUPS, NAV_ITEMS } from "@/app/navigation";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const active = findNavItem(useLocation().pathname);
  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div className={cn("flex h-14 items-center gap-2.5 px-4", collapsed && "justify-center px-0")}>
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
          <FileText className="size-4" aria-hidden />
        </div>
        {!collapsed && (
          <div className="min-w-0 leading-tight">
            <div className="truncate text-sm font-semibold text-foreground">ResumeFlow AI</div>
            <div className="truncate text-[11px] text-muted-foreground">Resume tailoring workspace</div>
          </div>
        )}
      </div>

      <nav aria-label="Main" className="flex-1 overflow-y-auto px-2 py-2">
        {NAV_GROUPS.map((group) => (
          <div key={group} className="mb-3">
            {!collapsed && (
              <div className="px-2.5 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/80">
                {group}
              </div>
            )}
            <ul className="space-y-0.5">
              {NAV_ITEMS.filter((item) => item.group === group).map((item) => {
                const index = NAV_ITEMS.indexOf(item) + 1;
                const link = (
                  <Link
                    to={item.path}
                    // One section is active at a time: /jobs/new must not also light up Jobs.
                    aria-current={active?.path === item.path ? "page" : undefined}
                    className={cn(
                        "group flex h-9 items-center gap-3 rounded-md px-2.5 text-sm font-medium outline-none transition-colors",
                        "hover:bg-sidebar-accent/70 hover:text-sidebar-accent-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50",
                        active?.path === item.path && "bg-sidebar-accent text-sidebar-accent-foreground",
                        collapsed && "justify-center px-0",
                    )}
                  >
                    <item.icon className="size-4 shrink-0" aria-hidden />
                    {collapsed ? <span className="sr-only">{item.label}</span> : <span className="flex-1 truncate">{item.label}</span>}
                    {!collapsed && (
                      <Kbd className="opacity-0 transition-opacity group-hover:opacity-100">Alt {index}</Kbd>
                    )}
                  </Link>
                );
                return (
                  <li key={item.path}>
                    {collapsed ? (
                      <Tooltip>
                        <TooltipTrigger asChild>{link}</TooltipTrigger>
                        <TooltipContent side="right">
                          {item.label} <Kbd className="border-background/20 bg-background/10 text-background">Alt {index}</Kbd>
                        </TooltipContent>
                      </Tooltip>
                    ) : (
                      link
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className={cn("border-t border-sidebar-border p-2", collapsed && "flex justify-center")}>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size={collapsed ? "icon-sm" : "sm"}
              onClick={onToggle}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={cn("text-muted-foreground", !collapsed && "w-full justify-start")}
            >
              {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
              {!collapsed && "Collapse"}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">
            Toggle sidebar <Kbd className="border-background/20 bg-background/10 text-background">Ctrl B</Kbd>
          </TooltipContent>
        </Tooltip>
      </div>
    </aside>
  );
}
