import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { clockLabel, fmt, linScale, niceYScale } from '../../components/ui';
import type { Bucket } from './buckets';

/**
 * Hand-rolled SVG event-density chart (DESIGN_FOUNDATION §Charts — no chart
 * library). Bars for discrete/gappy data, baselines at zero, ~4 gridlines, no
 * vertical grid, no axis box. Volume is the single accent; the *fault* portion
 * of each bar is the one licensed severity tint (color only where the data is a
 * severity). Empty buckets render as gaps, never interpolated.
 *
 * Emphasis-by-dimming: a hovered/selected bar stays full-opacity while the rest
 * dim (Health-app pattern). Left/right arrows move the selection for keyboard
 * traversal; Enter/Space activates (drills into the bucket's event list).
 */

interface Props {
  buckets: Bucket[];
  /** Currently selected bucket index, or null. */
  selected: number | null;
  onSelect: (index: number | null) => void;
  /** "as of HH:MM:SS" honesty stamp for stale-labeling. */
  asOf: string;
  /** Epoch seconds before which the chart has NO data coverage — monitoring had
   *  not started yet, or older events were not loaded (cap). The span from the
   *  window start to here is hatched and boundary-marked so un-collected time is
   *  never read as a genuinely-quiet network (honest empty state). */
  coverageStart?: number | null;
  /** Boundary caption, e.g. "monitoring since 08:14" or "older events not loaded". */
  coverageLabel?: string;
}

const HEIGHT = 200;
const PAD_TOP = 28;
const PAD_BOTTOM = 22;
const PAD_LEFT = 34;
const PAD_RIGHT = 8;

function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw && cw > 0) setW(Math.round(cw));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function EventDensityChart({
  buckets,
  selected,
  onSelect,
  asOf,
  coverageStart,
  coverageLabel,
}: Props) {
  const [wrapRef, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const hatchId = useId();

  const totals = useMemo(() => buckets.map((b) => b.total), [buckets]);
  const totalEvents = useMemo(() => totals.reduce((a, b) => a + b, 0), [totals]);
  const totalFaults = useMemo(
    () => buckets.reduce((a, b) => a + b.faults, 0),
    [buckets],
  );

  const y = useMemo(
    () => niceYScale(totals, { count: 4 }),
    [totals],
  );

  const innerW = Math.max(120, width - PAD_LEFT - PAD_RIGHT);
  const plotBottom = HEIGHT - PAD_BOTTOM;
  const plotTop = PAD_TOP;

  const xAt = useCallback(
    (i: number) => PAD_LEFT + (innerW / buckets.length) * i,
    [innerW, buckets.length],
  );
  const bandW = innerW / Math.max(1, buckets.length);
  const barW = Math.max(1, bandW - (bandW > 6 ? 1.5 : 0.5));
  const yScale = useMemo(
    () => linScale(y.min, y.max, plotBottom, plotTop),
    [y, plotBottom, plotTop],
  );

  const active = hover ?? selected;

  // Time labels: first, ~middle, last band start.
  const first = buckets[0];
  const last = buckets[buckets.length - 1];
  const mid = buckets[Math.floor(buckets.length / 2)];

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!buckets.length) return;
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const base = selected ?? (e.key === 'ArrowRight' ? -1 : buckets.length);
        const next = e.key === 'ArrowRight' ? base + 1 : base - 1;
        onSelect(Math.max(0, Math.min(buckets.length - 1, next)));
      } else if (e.key === 'Escape') {
        onSelect(null);
      }
    },
    [buckets.length, selected, onSelect],
  );

  const annIdx = active;
  const ann = annIdx != null ? buckets[annIdx] : null;

  // Data-coverage boundary: hatch the leading span the daemon never observed, so
  // it can't be mistaken for a quiet interior stretch (which draws as plain empty
  // buckets between real events). Map a timestamp to x through the same linear
  // band layout the bars use, then clamp inside the plot.
  const winStart = buckets[0]?.t0 ?? 0;
  const bWidth = buckets.length ? Math.max(1, buckets[0].t1 - buckets[0].t0) : 1;
  const xForTs = (t: number) =>
    PAD_LEFT + (innerW / Math.max(1, buckets.length)) * ((t - winStart) / bWidth);
  const coverageX =
    coverageStart != null && coverageStart > winStart
      ? Math.min(width - PAD_RIGHT, Math.max(PAD_LEFT, xForTs(coverageStart)))
      : null;
  const showCoverage = coverageX != null && coverageX > PAD_LEFT + 1;

  return (
    <div ref={wrapRef} className="w-full">
      {/* three-layer chart header: context label, summary stat, takeaway */}
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div>
          <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
            Event density
          </div>
          <div className="t-section tnum" style={{ color: 'var(--fg)' }}>
            {fmt(totalEvents)} events
            {totalFaults > 0 && (
              <span className="t-secondary tnum" style={{ color: 'var(--sev-p2)' }}>
                {'  ·  '}
                {fmt(totalFaults)} fault{totalFaults === 1 ? '' : 's'}
              </span>
            )}
          </div>
        </div>
        <div className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
          as of {asOf}
        </div>
      </div>

      <svg
        role="img"
        aria-label={`Event density, ${totalEvents} events across ${buckets.length} time buckets`}
        width="100%"
        height={HEIGHT}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onMouseLeave={() => setHover(null)}
        className="outline-none select-none"
        style={{ display: 'block' }}
      >
        {/* horizontal gridlines + y labels */}
        {y.ticks.map((t) => {
          const py = yScale(t);
          return (
            <g key={t}>
              <line
                x1={PAD_LEFT}
                x2={width - PAD_RIGHT}
                y1={py}
                y2={py}
                stroke="var(--grid)"
                strokeWidth={1}
              />
              <text
                x={PAD_LEFT - 6}
                y={py + 3}
                textAnchor="end"
                className="tnum"
                style={{ fontSize: 11, fill: 'var(--fg-subtle)' }}
              >
                {fmt(t)}
              </text>
            </g>
          );
        })}

        {/* data-coverage boundary: hatch + marker over the un-observed leading span */}
        {showCoverage && coverageX != null && (
          <g pointerEvents="none">
            <defs>
              <pattern
                id={hatchId}
                width={6}
                height={6}
                patternUnits="userSpaceOnUse"
                patternTransform="rotate(45)"
              >
                <line
                  x1={0}
                  y1={0}
                  x2={0}
                  y2={6}
                  stroke="var(--fg-subtle)"
                  strokeWidth={1}
                  opacity={0.35}
                />
              </pattern>
            </defs>
            <rect
              x={PAD_LEFT}
              y={plotTop}
              width={coverageX - PAD_LEFT}
              height={plotBottom - plotTop}
              fill={`url(#${hatchId})`}
            />
            <line
              x1={coverageX}
              x2={coverageX}
              y1={plotTop}
              y2={plotBottom}
              stroke="var(--fg-subtle)"
              strokeWidth={1}
              strokeDasharray="2 2"
            />
            {coverageLabel && (
              <text
                x={coverageX + 4}
                y={plotTop + 10}
                textAnchor="start"
                className="tnum"
                style={{ fontSize: 10, fill: 'var(--fg-subtle)' }}
              >
                {coverageLabel}
              </text>
            )}
          </g>
        )}

        {/* bars: routine volume (accent) + fault cap (severity tint) */}
        {buckets.map((b, i) => {
          if (b.total === 0) return null; // gap stays a gap
          const x = xAt(i) + (bandW - barW) / 2;
          const topY = yScale(b.total);
          const baseY = yScale(0);
          const faultH = b.faults > 0 ? baseY - yScale(b.faults) : 0;
          const routineTop = yScale(b.total - b.faults);
          const dim = active != null && active !== i;
          return (
            <g key={i} opacity={dim ? 0.32 : 1} style={{ transition: 'opacity 120ms' }}>
              {/* routine portion */}
              {b.total - b.faults > 0 && (
                <rect
                  x={x}
                  y={routineTop}
                  width={barW}
                  height={Math.max(0.5, baseY - routineTop)}
                  fill="var(--accent)"
                  rx={bandW > 6 ? 1 : 0}
                />
              )}
              {/* fault cap */}
              {faultH > 0 && (
                <rect
                  x={x}
                  y={topY}
                  width={barW}
                  height={Math.max(0.5, routineTop - topY)}
                  fill="var(--sev-p2)"
                  rx={bandW > 6 ? 1 : 0}
                />
              )}
            </g>
          );
        })}

        {/* invisible hit targets across every band (incl. empty) */}
        {buckets.map((_b, i) => (
          <rect
            key={`hit-${i}`}
            x={xAt(i)}
            y={plotTop}
            width={bandW}
            height={plotBottom - plotTop}
            fill="transparent"
            style={{ cursor: 'pointer' }}
            onMouseEnter={() => setHover(i)}
            onClick={() => onSelect(selected === i ? null : i)}
          />
        ))}

        {/* selection crosshair + single annotation */}
        {ann && annIdx != null && (
          <g pointerEvents="none">
            <line
              x1={xAt(annIdx) + bandW / 2}
              x2={xAt(annIdx) + bandW / 2}
              y1={plotTop}
              y2={plotBottom}
              stroke="var(--fg-subtle)"
              strokeWidth={1}
              strokeDasharray="2 2"
            />
            <text
              x={Math.min(width - PAD_RIGHT, Math.max(PAD_LEFT + 40, xAt(annIdx) + bandW / 2))}
              y={plotTop - 12}
              textAnchor="middle"
              className="tnum"
              style={{ fontSize: 12, fill: 'var(--fg)', fontWeight: 500 }}
            >
              {fmt(ann.total)} event{ann.total === 1 ? '' : 's'}
              {ann.faults > 0 ? `, ${fmt(ann.faults)} fault${ann.faults === 1 ? '' : 's'}` : ''}
            </text>
            <text
              x={Math.min(width - PAD_RIGHT, Math.max(PAD_LEFT + 40, xAt(annIdx) + bandW / 2))}
              y={plotTop - 26}
              textAnchor="middle"
              className="tnum"
              style={{ fontSize: 11, fill: 'var(--fg-subtle)' }}
            >
              {clockLabel(ann.t0)}
            </text>
          </g>
        )}

        {/* x axis labels */}
        {[first, mid, last].map((b, i) => {
          if (!b) return null;
          const idx = i === 0 ? 0 : i === 1 ? Math.floor(buckets.length / 2) : buckets.length - 1;
          const anchor = i === 0 ? 'start' : i === 1 ? 'middle' : 'end';
          return (
            <text
              key={`x-${i}`}
              x={i === 0 ? PAD_LEFT : i === 2 ? width - PAD_RIGHT : xAt(idx) + bandW / 2}
              y={HEIGHT - 6}
              textAnchor={anchor}
              className="tnum"
              style={{ fontSize: 11, fill: 'var(--fg-subtle)' }}
            >
              {clockLabel(b.t0)}
            </text>
          );
        })}
      </svg>

      {/* legend */}
      <div className="flex items-center gap-4 mt-1.5">
        <LegendSwatch color="var(--accent)" label="Events" />
        <LegendSwatch color="var(--sev-p2)" label="Faults" />
        <span className="t-caption ml-auto" style={{ color: 'var(--fg-subtle)' }}>
          {selected != null ? 'Click a bar again to clear' : 'Click a bar to inspect its events'}
        </span>
      </div>
    </div>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 t-caption" style={{ color: 'var(--fg-muted)' }}>
      <span
        aria-hidden
        className="inline-block rounded-[2px]"
        style={{ width: 10, height: 10, background: color }}
      />
      {label}
    </span>
  );
}
