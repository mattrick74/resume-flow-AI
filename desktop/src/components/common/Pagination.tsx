import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/ui/select";

export const PAGE_SIZES = [25, 50, 100] as const;

/** Page controls under a list: which rows are shown, previous/next, and the page size. Pages count from 1. */
export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  label = "items",
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
  label?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const first = total ? (page - 1) * pageSize + 1 : 0;
  const last = Math.min(page * pageSize, total);
  return (
    <nav className="mt-3 flex flex-wrap items-center gap-3 text-sm" aria-label={`Pages of ${label}`}>
      <span className="text-muted-foreground tabular-nums" role="status">
        {first}–{last} of {total} {label}
      </span>
      <div className="ml-auto flex items-center gap-2">
        <label className="flex items-center gap-2 text-muted-foreground">
          Per page
          <NativeSelect value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))} className="h-8 w-20" aria-label="Rows per page">
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </NativeSelect>
        </label>
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          <ChevronLeft /> Previous
        </Button>
        <span className="tabular-nums">
          Page {page} of {pages}
        </span>
        <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          Next <ChevronRight />
        </Button>
      </div>
    </nav>
  );
}
