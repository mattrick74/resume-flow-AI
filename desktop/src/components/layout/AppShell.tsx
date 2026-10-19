import { useCallback, useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router";

import { NAV_ITEMS } from "@/app/navigation";
import { EngineOfflineBanner } from "@/components/layout/BackendGate";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { Sidebar } from "@/components/layout/Sidebar";
import { TopBar } from "@/components/layout/TopBar";

const SIDEBAR_KEY = "resumeflow.sidebarCollapsed";

function readCollapsed() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "1";
  } catch {
    return false;
  }
}

export function AppShell() {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const toggleSidebar = useCallback(() => setCollapsed((value) => !value), []);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
    } catch {
      // preference only
    }
  }, [collapsed]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      } else if (mod && !event.altKey && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggleSidebar();
      } else if (event.altKey && !mod) {
        const match = /^Digit([1-8])$/.exec(event.code);
        if (match) {
          event.preventDefault();
          navigate(NAV_ITEMS[Number(match[1]) - 1].path);
        }
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate, toggleSidebar]);

  return (
    <div className="flex h-full">
      <Sidebar collapsed={collapsed} onToggle={toggleSidebar} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onOpenPalette={() => setPaletteOpen(true)} />
        <EngineOfflineBanner />
        {/* `relative` keeps absolutely positioned descendants (e.g. Radix form inputs) inside the scroll area. */}
        <main className="relative flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl px-8 py-8">
            <Outlet />
          </div>
        </main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} onToggleSidebar={toggleSidebar} />
    </div>
  );
}
