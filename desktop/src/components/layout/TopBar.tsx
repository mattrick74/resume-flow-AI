import { Monitor, Moon, Search, Sun } from "lucide-react";
import { useLocation } from "react-router";

import { findNavItem } from "@/app/navigation";
import { useChangeTheme, useTheme } from "@/app/theme";
import { EngineStatus } from "@/components/layout/EngineStatus";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { ThemePreference } from "@/lib/types";

const NEXT_THEME: Record<ThemePreference, ThemePreference> = { system: "light", light: "dark", dark: "system" };
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon };

export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { pathname } = useLocation();
  const item = findNavItem(pathname);
  const { preference } = useTheme();
  const changeTheme = useChangeTheme();
  const ThemeIcon = THEME_ICON[preference];

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-background/80 px-6 backdrop-blur">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        {item && <span className="text-muted-foreground">{item.group}</span>}
        {item && <span className="text-muted-foreground/50">/</span>}
        <span className="truncate font-medium">{item?.label ?? "Not found"}</span>
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          onClick={onOpenPalette}
          className="w-56 justify-start text-muted-foreground"
          aria-keyshortcuts="Control+K"
        >
          <Search />
          <span className="flex-1 text-left">Search or jump to…</span>
          <Kbd>Ctrl K</Kbd>
        </Button>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => changeTheme(NEXT_THEME[preference])}
              aria-label={`Theme: ${preference}. Switch to ${NEXT_THEME[preference]}`}
            >
              <ThemeIcon />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Theme: {preference}</TooltipContent>
        </Tooltip>
        <EngineStatus />
      </div>
    </header>
  );
}
