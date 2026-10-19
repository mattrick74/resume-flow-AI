import { CornerDownLeft, Monitor, Moon, PanelLeft, Search, Sun, type LucideIcon } from "lucide-react";
import { useMemo, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router";

import { NAV_ITEMS } from "@/app/navigation";
import { useChangeTheme } from "@/app/theme";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

interface Command {
  id: string;
  label: string;
  hint: string;
  icon: LucideIcon;
  run: () => void;
}

export function CommandPalette({
  open,
  onOpenChange,
  onToggleSidebar,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onToggleSidebar: () => void;
}) {
  const navigate = useNavigate();
  const changeTheme = useChangeTheme();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const commands = useMemo<Command[]>(
    () => [
      ...NAV_ITEMS.map((item, index) => ({
        id: `nav:${item.path}`,
        label: `Go to ${item.label}`,
        hint: `Alt ${index + 1}`,
        icon: item.icon,
        run: () => navigate(item.path),
      })),
      { id: "theme:light", label: "Use light theme", hint: "Theme", icon: Sun, run: () => changeTheme("light") },
      { id: "theme:dark", label: "Use dark theme", hint: "Theme", icon: Moon, run: () => changeTheme("dark") },
      { id: "theme:system", label: "Match system theme", hint: "Theme", icon: Monitor, run: () => changeTheme("system") },
      { id: "sidebar", label: "Toggle sidebar", hint: "Ctrl B", icon: PanelLeft, run: onToggleSidebar },
    ],
    [navigate, changeTheme, onToggleSidebar],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? commands.filter((c) => c.label.toLowerCase().includes(needle)) : commands;
  }, [commands, query]);

  const close = () => {
    onOpenChange(false);
    setQuery("");
    setActive(0);
  };

  const runCommand = (command: Command | undefined) => {
    if (!command) return;
    close();
    command.run();
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (filtered.length ? (i + 1) % filtered.length : 0));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (filtered.length ? (i - 1 + filtered.length) % filtered.length : 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      runCommand(filtered[active]);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? onOpenChange(true) : close())}>
      <DialogContent onKeyDown={onKeyDown} aria-describedby="command-palette-help">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription id="command-palette-help" className="sr-only">
          Type to filter commands, use arrow keys to choose, Enter to run.
        </DialogDescription>
        <div className="flex items-center gap-2 border-b px-4">
          <Search className="size-4 text-muted-foreground" aria-hidden />
          <input
            autoFocus
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActive(0);
            }}
            placeholder="Search commands and screens…"
            aria-label="Search commands"
            aria-controls="command-list"
            aria-activedescendant={filtered[active] ? `cmd-${filtered[active].id}` : undefined}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          <Kbd>Esc</Kbd>
        </div>
        <ul id="command-list" role="listbox" aria-label="Commands" className="max-h-80 overflow-y-auto p-1.5">
          {filtered.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No matching commands</li>}
          {filtered.map((command, index) => (
            <li
              key={command.id}
              id={`cmd-${command.id}`}
              role="option"
              aria-selected={index === active}
              onMouseMove={() => setActive(index)}
              onClick={() => runCommand(command)}
              className={cn(
                "flex cursor-default items-center gap-3 rounded-md px-3 py-2 text-sm",
                index === active && "bg-accent text-accent-foreground",
              )}
            >
              <command.icon className="size-4 text-muted-foreground" aria-hidden />
              <span className="flex-1">{command.label}</span>
              {index === active ? <CornerDownLeft className="size-3.5 text-muted-foreground" aria-hidden /> : <Kbd>{command.hint}</Kbd>}
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
