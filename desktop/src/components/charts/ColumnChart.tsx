import { useLayoutEffect, useRef, useState } from "react";

export interface ColumnDatum {
  key: string;
  label: string; // axis/tooltip label
  value: number;
}

function niceMax(value: number) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const steps = [1, 2, 2.5, 5, 10];
  const normalized = value / magnitude;
  return (steps.find((s) => s >= normalized) ?? 10) * magnitude;
}

function topRoundedBar(x: number, y: number, w: number, h: number) {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}L${x},${y + r}Q${x},${y} ${x + r},${y}L${x + w - r},${y}Q${x + w},${y} ${x + w},${y + r}L${x + w},${y + h}Z`;
}

/**
 * Single-series column chart: thin bars from one baseline, 4px rounded data ends,
 * hairline gridlines, per-column hover tooltip, optional labeled reference line.
 */
export function ColumnChart({
  data,
  format,
  reference,
  color = "var(--series-1)",
  height = 200,
  ariaLabel,
}: {
  data: ColumnDatum[];
  format: (value: number) => string;
  reference?: { value: number; label: string };
  color?: string;
  height?: number;
  ariaLabel: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useLayoutEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  const margin = { top: 12, right: 8, bottom: 24, left: 44 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const peak = Math.max(...data.map((d) => d.value), reference?.value ?? 0);
  const max = niceMax(peak);
  const ticks = [0, max / 4, max / 2, (3 * max) / 4, max];
  const band = plotW / Math.max(data.length, 1);
  const barW = Math.min(24, Math.max(2, band * 0.6));
  const y = (v: number) => margin.top + plotH - (v / max) * plotH;
  const labelEvery = Math.ceil(data.length / Math.max(1, Math.floor(plotW / 64)));

  return (
    <div ref={container} className="relative w-full">
      <svg width={width} height={height} role="img" aria-label={ariaLabel} className="block overflow-visible">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={margin.left} x2={width - margin.right} y1={y(tick)} y2={y(tick)} stroke="var(--border)" strokeWidth={1} />
            <text x={margin.left - 8} y={y(tick)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px] tabular-nums">
              {format(tick)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const cx = margin.left + band * i + band / 2;
          const h = Math.max(0, (d.value / max) * plotH);
          return (
            <g key={d.key}>
              {h > 0 && <path d={topRoundedBar(cx - barW / 2, y(d.value), barW, h)} fill={color} opacity={hover === null || hover === i ? 1 : 0.55} />}
              {i % labelEvery === 0 && (
                <text x={cx} y={height - 6} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                  {d.label}
                </text>
              )}
              <rect
                x={margin.left + band * i}
                y={margin.top}
                width={band}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              >
                <title>{`${d.label}: ${format(d.value)}`}</title>
              </rect>
            </g>
          );
        })}
        {reference && reference.value > 0 && (
          <g>
            <line x1={margin.left} x2={width - margin.right} y1={y(reference.value)} y2={y(reference.value)} stroke="var(--muted-foreground)" strokeWidth={1} />
            <text x={width - margin.right} y={y(reference.value) - 4} textAnchor="end" className="fill-muted-foreground text-[10px]">
              {reference.label}
            </text>
          </g>
        )}
        <line x1={margin.left} x2={width - margin.right} y1={y(0)} y2={y(0)} stroke="var(--input)" strokeWidth={1} />
      </svg>
      {hover !== null && data[hover] && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-md"
          style={{ left: margin.left + band * hover + band / 2, top: y(data[hover].value) - 6 }}
          role="status"
        >
          <div className="text-muted-foreground">{data[hover].label}</div>
          <div className="font-semibold tabular-nums">{format(data[hover].value)}</div>
        </div>
      )}
    </div>
  );
}
