import { FolderOpen, Search } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState, describeError } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { PAGE_SIZES, Pagination } from "@/components/common/Pagination";
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
  const [params, setParams] = useSearchParams();
  const filters = {
    q: params.get("q") ?? "",
    status: params.get("status") ?? "",
    candidate_id: params.get("candidate_id") ?? "",
    date_from: params.get("date_from") ?? "",
    date_to: params.get("date_to") ?? "",
  };
  const size: number = PAGE_SIZES.find((value) => String(value) === params.get("size")) ?? PAGE_SIZES[0];
  const page = Math.max(1, Number.parseInt(params.get("page") ?? "1", 10) || 1);
  const candidates = useCandidates({});
  const apps = useApplications({
    ...Object.fromEntries(Object.entries(filters).filter(([, value]) => value)),
    limit: size,
    offset: (page - 1) * size,
  });
  const visible = apps.data?.items ?? [];
  const total = apps.data?.total ?? 0;

  const setFilter = (key: keyof typeof filters, value: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        next.delete("page");
        return next;
      },
      { replace: true },
    );
  const setPaging = (nextPage: number, nextSize = size) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (nextPage > 1) next.set("page", String(nextPage));
        else next.delete("page");
        if (nextSize !== PAGE_SIZES[0]) next.set("size", String(nextSize));
        else next.delete("size");
        return next;
      },
      { replace: true },
    );

  // If filters or deletions make an old URL's page empty, return to the final page that still has rows.
  useEffect(() => {
    if (apps.data && !apps.isPlaceholderData && page > 1 && visible.length === 0 && total > 0) setPaging(Math.ceil(total / size));
  });
  const openFolder = (path: string) => api.system.open(path).catch((e) => toast.error("Couldn't open folder", { description: describeError(e) }));

  return (
    <>
      <PageHeader title="Applications" description="Every generated application, with its documents, source JD, URL and metadata." />
      <Card className="mb-4 grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-[2fr_1.2fr_1.2fr_1fr_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor="app-search">Search</Label>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input id="app-search" value={filters.q} onChange={(e) => setFilter("q", e.target.value)} placeholder="Employer, title or candidate" className="pl-8" />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-status">Status</Label>
          <NativeSelect id="app-status" value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-candidate">Candidate</Label>
          <NativeSelect id="app-candidate" value={filters.candidate_id} onChange={(e) => setFilter("candidate_id", e.target.value)}>
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
          <Input id="app-from" type="date" value={filters.date_from} onChange={(e) => setFilter("date_from", e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="app-to">To</Label>
          <Input id="app-to" type="date" value={filters.date_to} onChange={(e) => setFilter("date_to", e.target.value)} />
        </div>
      </Card>

      {apps.isPending ? (
        <Skeleton className="h-72" />
      ) : apps.isError ? (
        <ErrorState error={apps.error} onRetry={() => void apps.refetch()} />
      ) : visible.length === 0 ? (
        <Card>
          <EmptyState
            icon={FolderOpen}
            title="No applications match"
            description="Applications appear here once you choose a candidate for a job. Files are saved under date / daily number and profile name."
            className="py-14"
          />
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="border-b px-4 py-2 text-xs text-muted-foreground">{total} application(s)</div>
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
              {visible.map((app) => (
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
      {total > 0 && <Pagination page={page} pageSize={size} total={total} onPage={(next) => setPaging(next)} onPageSize={(next) => setPaging(1, next)} label="applications" />}
    </>
  );
}
