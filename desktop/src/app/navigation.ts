import {
  Briefcase,
  ChartNoAxesColumn,
  ClipboardCheck,
  FilePlus2,
  FolderOpen,
  LayoutDashboard,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavGroup = "Workflow" | "Records" | "Insights" | "System";

export interface NavItem {
  path: string;
  label: string;
  icon: LucideIcon;
  group: NavGroup;
  description: string;
}

/** Order defines the Alt+1…Alt+8 shortcuts. */
export const NAV_ITEMS: NavItem[] = [
  {
    path: "/",
    label: "Dashboard",
    icon: LayoutDashboard,
    group: "Workflow",
    description: "Daily throughput, review backlog and spend",
  },
  {
    path: "/jobs/new",
    label: "New Job",
    icon: FilePlus2,
    group: "Workflow",
    description: "Paste one or several job descriptions",
  },
  {
    path: "/jobs",
    label: "Jobs",
    icon: Briefcase,
    group: "Workflow",
    description: "Saved jobs, one-click generation and ranked candidates",
  },
  {
    path: "/review",
    label: "Review Queue",
    icon: ClipboardCheck,
    group: "Workflow",
    description: "Check, edit and approve generated documents",
  },
  {
    path: "/candidates",
    label: "Candidates",
    icon: Users,
    group: "Records",
    description: "Verified candidate profiles and resume versions",
  },
  {
    path: "/applications",
    label: "Applications",
    icon: FolderOpen,
    group: "Records",
    description: "Searchable history of every generated application",
  },
  {
    path: "/analytics",
    label: "Analytics",
    icon: ChartNoAxesColumn,
    group: "Insights",
    description: "Throughput, model costs, latency and retries",
  },
  {
    path: "/settings",
    label: "Settings",
    icon: Settings,
    group: "System",
    description: "API key, models, output folder, budgets and theme",
  },
];

export const NAV_GROUPS: NavGroup[] = ["Workflow", "Records", "Insights", "System"];

export function findNavItem(pathname: string): NavItem | undefined {
  if (pathname === "/") return NAV_ITEMS[0];
  // "/jobs/new" is listed before "/jobs", so detail pages like /jobs/:id fall through to Jobs.
  return NAV_ITEMS.find((item) => item.path !== "/" && (pathname === item.path || pathname.startsWith(`${item.path}/`)));
}
