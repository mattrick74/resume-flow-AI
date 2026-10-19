import { CircleCheck, CircleX, Copy, FileUp, LoaderCircle, ScanText, Search, Upload, Users } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router";

import { ReadinessBadge, SkillCounts } from "@/components/candidates/CandidateBadges";
import { ReextractAllDialog } from "@/components/candidates/Reextract";
import { useResumeImport, type ImportItem } from "@/components/candidates/useResumeImport";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageHeader } from "@/components/common/PageHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCandidates } from "@/lib/queries";
import type { CandidateListItem } from "@/lib/types";
import { cn, formatRelativeTime } from "@/lib/utils";

type Filter = "active" | "archived" | "all";

export function CandidatesPage() {
  const [filter, setFilter] = useState<Filter>("active");
  const [search, setSearch] = useState("");
  const candidates = useCandidates({ status: filter === "all" ? undefined : filter, q: search || undefined });
  const importer = useResumeImport();
  const [rereading, setRereading] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  const onDragEnter = (event: DragEvent) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    dragDepth.current += 1;
    setDragging(true);
  };
  const onDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    if (event.dataTransfer.files.length) importer.add(event.dataTransfer.files);
  };

  return (
    <div
      className="relative"
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDragOver={(event) => event.preventDefault()}
      onDrop={onDrop}
    >
      <PageHeader
        title="Candidates"
        description="Candidate profiles. Only what's in a profile is used for matching and writing."
        actions={
          <>
            <input
              ref={input}
              type="file"
              accept=".docx,.pdf"
              multiple
              hidden
              aria-label="Choose resume files"
              onChange={(event) => {
                if (event.target.files?.length) importer.add(event.target.files);
                event.target.value = "";
              }}
            />
            <Button variant="outline" disabled={!candidates.data?.length} onClick={() => setRereading(true)}>
              <ScanText /> Re-read all resumes
            </Button>
            <Button onClick={() => input.current?.click()}>
              <Upload /> Import resumes
            </Button>
          </>
        }
      />
      <ReextractAllDialog candidates={candidates.data ?? []} open={rereading} onOpenChange={setRereading} />

      {importer.items.length > 0 && <ImportProgress items={importer.items} busy={importer.busy} onClear={importer.clearFinished} />}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented<Filter>
          ariaLabel="Candidate status"
          value={filter}
          onValueChange={setFilter}
          options={[
            { value: "active", label: "Active" },
            { value: "archived", label: "Archived" },
            { value: "all", label: "All" },
          ]}
        />
        <div className="relative w-64">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name or email" className="pl-8" aria-label="Search candidates" />
        </div>
      </div>

      {candidates.isPending ? (
        <Skeleton className="h-72" />
      ) : candidates.isError ? (
        <ErrorState error={candidates.error} onRetry={() => void candidates.refetch()} />
      ) : candidates.data.length === 0 ? (
        <Card>
          <EmptyState
            icon={Users}
            title={search ? "No candidates match your search" : "No candidates yet"}
            description="Import each candidate's original resume (DOCX or text-based PDF), or drop files anywhere on this page."
            actions={
              <Button variant="outline" size="sm" onClick={() => input.current?.click()}>
                <FileUp /> Choose files
              </Button>
            }
            className="py-16"
          />
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <CandidateTable items={candidates.data} />
        </Card>
      )}

      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-40 flex items-center justify-center bg-primary/10 backdrop-blur-[1px]">
          <div className="rounded-2xl border-2 border-dashed border-primary bg-card px-10 py-8 text-center shadow-xl">
            <FileUp className="mx-auto mb-2 size-8 text-primary" aria-hidden />
            <div className="font-medium">Drop resumes to import</div>
            <div className="text-sm text-muted-foreground">DOCX or text-based PDF, up to 10 MB each</div>
          </div>
        </div>
      )}
    </div>
  );
}

function CandidateTable({ items }: { items: CandidateListItem[] }) {
  const navigate = useNavigate();
  return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Location</TableHead>
            <TableHead>Resume</TableHead>
            <TableHead>Skills</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Applications</TableHead>
            <TableHead className="text-right">Updated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((c) => (
            <TableRow key={c.id} className="cursor-pointer" onClick={() => navigate(`/candidates/${c.id}`)}>
              <TableCell>
                <Link to={`/candidates/${c.id}`} className="font-medium outline-none hover:underline focus-visible:underline" onClick={(e) => e.stopPropagation()}>
                  {c.full_name}
                </Link>
                <div className="text-xs text-muted-foreground">{c.email ?? "No email"}</div>
              </TableCell>
              <TableCell className="text-muted-foreground">{c.location ?? "—"}</TableCell>
              <TableCell className="tabular-nums text-muted-foreground">{c.latest_version ? `v${c.latest_version}` : "—"}</TableCell>
              <TableCell>
                <SkillCounts skills={c.skills} />
              </TableCell>
              <TableCell>
                {c.status === "archived" ? <span className="text-xs text-muted-foreground">Archived</span> : <ReadinessBadge readiness={c.readiness} consent={c.consent_confirmed} />}
              </TableCell>
              <TableCell className="text-right tabular-nums">{c.applications}</TableCell>
              <TableCell className="text-right text-xs text-muted-foreground">{formatRelativeTime(c.updated_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
  );
}

function ImportProgress({ items, busy, onClear }: { items: ImportItem[]; busy: boolean; onClear: () => void }) {
  const done = items.filter((i) => i.status === "done").length;
  return (
    <Card className="mb-4">
      <div className="flex items-center gap-3 border-b px-4 py-2.5">
        <span className="text-sm font-medium">
          {busy ? "Importing resumes…" : "Import finished"} <span className="text-muted-foreground">({done} of {items.length} imported)</span>
        </span>
        {!busy && (
          <Button variant="ghost" size="sm" className="ml-auto" onClick={onClear}>
            Dismiss
          </Button>
        )}
      </div>
      <ul className="max-h-60 divide-y overflow-y-auto">
        {items.map((item) => (
          <li key={item.key} className="flex items-center gap-3 px-4 py-2 text-sm">
            <StatusIcon status={item.status} />
            <span className="min-w-0 flex-1 truncate">{item.name}</span>
            <span className={cn("max-w-[55%] truncate text-xs", item.status === "error" ? "text-destructive" : "text-muted-foreground")}>
              {item.status === "queued" ? "Waiting" : item.status === "uploading" ? "Reading resume…" : item.message}
            </span>
            {item.candidateId && (item.status === "done" || item.status === "duplicate") && (
              <Button asChild variant="link" size="sm" className="h-auto px-0">
                <Link to={`/candidates/${item.candidateId}`}>{item.status === "done" ? "Review" : "Open"}</Link>
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function StatusIcon({ status }: { status: ImportItem["status"] }) {
  if (status === "uploading") return <LoaderCircle className="size-4 animate-spin text-primary" aria-label="Uploading" />;
  if (status === "done") return <CircleCheck className="size-4 text-success" aria-label="Imported" />;
  if (status === "duplicate") return <Copy className="size-4 text-warning" aria-label="Duplicate" />;
  if (status === "error") return <CircleX className="size-4 text-destructive" aria-label="Failed" />;
  return <span className="size-4 rounded-full border-2 border-muted" aria-label="Queued" />;
}
