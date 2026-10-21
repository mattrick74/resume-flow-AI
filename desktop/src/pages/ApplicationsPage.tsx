import { FolderOpen, Search } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState, describeError } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api } from "@/lib/api";
import { useApplications, useCandidates } from "@/lib/queries";
import type { PipelineStatus } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const STATUSES: { value: string; label: string }[] = [
  { value: "", label: "Any status" },
  { value: "EXPORTED", label: "Exported" },
  { value: "SUBMITTED", label: "Submitted" },
  { value: "INTERVIEW", label: "Interview" },
  { value: "OFFER", label: "Offer" },
  { value: "REJECTED", label: "Rejected" },
  { value: "FAILED", label: "Failed" },
  { value: "GENERATING,VALIDATING,MATCHED,AWAITING_REVIEW,APPROVED", label: "In progress" },
];

export function ApplicationsPage() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState({ q: "", status: "", candidate_id: "", date_from: "", date_to: "" });
  const candidates = useCandidates({});
  const apps = useApplications(Object.fromEntries(Object.entries(filters).filter(([, v]) => v)));
  const set = (key: keyof typeof filters, value: string) => setFilters((f) => ({ ...f, [key]: value }));
  const openFolder = (path: string) => api.system.open(path).catch((e) => toast.error("Couldn't open folder", { description: describeError(e) }));

  return (
    <>
      <PageHeader title="Applications" description="Every generated application, with its documents, source JD, URL and metadata." />
      <Card className="mb-4 grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-[2fr_1.2fr_1.2fr_1fr_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor="app-search">Search</Label>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input id="app-search" value={filters.q} onChange={(e) => set("q", e.target.value)} placeholder="Employer, title or candidate" className="pl-8" />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-status">Status</Label>
          <NativeSelect id="app-status" value={filters.status} onChange={(e) => set("status", e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-candidate">Candidate</Label>
          <NativeSelect id="app-candidate" value={filters.candidate_id} onChange={(e) => set("candidate_id", e.target.value)}>
            <option value="">Anyone</option>
            {candidates.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.full_name}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-from">From</Label>
          <Input id="app-from" type="date" value={filters.date_from} onChange={(e) => set("date_from", e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-to">To</Label>
          <Input id="app-to" type="date" value={filters.date_to} onChange={(e) => set("date_to", e.target.value)} />
        </div>
      </Card>

      {apps.isPending ? (
        <Skeleton className="h-72" />
      ) : apps.isError ? (
        <ErrorState error={apps.error} onRetry={() => void apps.refetch()} />
      ) : apps.data.length === 0 ? (
        <Card>
          <EmptyState
            icon={FolderOpen}
            title="No applications match"
            description="Applications appear here once you choose a candidate for a job. Exported files are saved to Applications\YYYY-MM-DD\Candidate_Name\site\Position_-_url\."
            className="py-14"
          />
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="border-b px-4 py-2 text-xs text-muted-foreground">{apps.data.length} application(s)</div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Candidate</TableHead>
                <TableHead>Job</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Files</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {apps.data.map((app) => (
                <TableRow key={app.id} className="cursor-pointer" onClick={() => navigate(`/applications/${app.id}`)}>
                  <TableCell className="whitespace-nowrap tabular-nums">
                    {formatDate(app.application_date)}
                    <span className="ml-1.5 text-xs text-muted-foreground">#{String(app.sequence_number).padStart(3, "0")}</span>
                  </TableCell>
                  <TableCell>
                    <Link to={`/applications/${app.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                      {app.candidate_name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {app.title ?? "Job"}
                    <span className="text-muted-foreground"> · {app.company ?? "—"}</span>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={app.status as PipelineStatus} />
                  </TableCell>
                  <TableCell className="text-right">
                    {app.output_directory ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          void openFolder(app.output_directory!);
                        }}
                      >
                        <FolderOpen /> Open folder
                      </Button>
                    ) : (
                      <span className="text-xs text-muted-foreground">Not exported</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </>
  );
}
