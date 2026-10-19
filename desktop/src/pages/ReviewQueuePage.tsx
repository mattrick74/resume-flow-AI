import { useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, FileDown, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";

import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState, describeError } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { queryKeys, useApplications } from "@/lib/queries";
import type { ApplicationSummary } from "@/lib/types";
import { formatRelativeTime } from "@/lib/utils";

const VIEWS = {
  review: { label: "Awaiting review", status: "AWAITING_REVIEW", empty: "Nothing is waiting for review." },
  progress: { label: "In progress", status: "MATCHED,GENERATING,VALIDATING", empty: "No documents are being generated." },
  approved: { label: "Ready to export", status: "APPROVED", empty: "No approved applications are waiting to be exported." },
  attention: { label: "Needs attention", status: "FAILED,EXPORTED", empty: "Nothing needs attention." },
} as const;
type View = keyof typeof VIEWS;

export function ReviewQueuePage() {
  const [view, setView] = useState<View>("review");
  const navigate = useNavigate();
  const client = useQueryClient();
  const apps = useApplications({ status: VIEWS[view].status }, { poll: true });
  const [exporting, setExporting] = useState(false);
  // "Needs attention" = failed generation or failed PDF conversion.
  const rows = (apps.data ?? []).filter((a) => view !== "attention" || a.status === "FAILED" || a.pdf_status === "failed");

  const exportAll = async (items: ApplicationSummary[]) => {
    setExporting(true);
    let ok = 0;
    for (const item of items) {
      try {
        await api.applications.export(item.id);
        ok += 1;
      } catch (error) {
        toast.error(`Couldn't export ${item.candidate_name}`, { description: describeError(error) });
      }
    }
    setExporting(false);
    toast.success(`${ok} export${ok === 1 ? "" : "s"} queued`);
    void client.invalidateQueries({ queryKey: queryKeys.applications });
  };

  return (
    <>
      <PageHeader
        title="Review Queue"
        description="Nothing is exported until you approve it."
        actions={
          view === "approved" && rows.length > 0 ? (
            <Button onClick={() => void exportAll(rows)} disabled={exporting}>
              {exporting ? <LoaderCircle className="animate-spin" /> : <FileDown />} Export all {rows.length}
            </Button>
          ) : undefined
        }
      />
      <Tabs value={view} onValueChange={(v) => setView(v as View)}>
        <TabsList className="mb-4">
          {(Object.keys(VIEWS) as View[]).map((key) => (
            <TabsTrigger key={key} value={key}>
              {VIEWS[key].label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {apps.isPending ? (
        <Skeleton className="h-64" />
      ) : apps.isError ? (
        <ErrorState error={apps.error} onRetry={() => void apps.refetch()} />
      ) : rows.length === 0 ? (
        <Card>
          <EmptyState icon={ClipboardCheck} title={VIEWS[view].empty} description="Applications move here automatically as documents are generated, approved and exported." className="py-14" />
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Candidate</TableHead>
                <TableHead>Job</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Note</TableHead>
                <TableHead className="text-right">Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((app) => (
                <TableRow key={app.id} className="cursor-pointer" onClick={() => navigate(`/applications/${app.id}`)}>
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
                    <StatusBadge status={app.status} />
                  </TableCell>
                  <TableCell className="max-w-72 truncate text-xs text-muted-foreground">
                    {app.status === "FAILED" ? app.error : app.pdf_status === "failed" ? "PDF conversion failed" : app.selection_method === "manual_override" ? "Manually selected" : ""}
                  </TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">{formatRelativeTime(app.updated_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </>
  );
}
