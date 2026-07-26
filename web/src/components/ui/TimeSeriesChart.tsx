import {
  type MouseEvent as ReactMouseEvent,
  type RefObject,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import {
  areaPaths,
  bandPaths,
  clockLabel,
  clockLabelSec,
  dayLabel,
  dayTimeLabel,
  fmt,
  linePath,
  linScale,
  nearestIndex,
  niceYScale,
  type ChartPoint,
  type Scale,
} from './chart-utils';
import { cn } from './cn';

/** Finite points with no finite neighbour on either side — an isolated sample a
 *  line can't connect. Drawn as dots so a lone real reading (or a single-bucket
 *  window) stays visible instead of vanishing between gaps. */
function isolatedPoints(points: ChartPoint[]): ChartPoint[] {
  const finite = (p: ChartPoint | undefined): boolean =>
    p != null && p.value != null && Number.isFinite(p.value);
  const out: ChartPoint[] = [];
  for (let i = 0; i < points.length; i++) {
    if (finite(points[i]) && !finite(points[i - 1]) && !finite(points[i + 1])) {
      out.push(points[i]);
    }
  }
  return out;
}

/**
 * Hand-rolled time-series chart obeying docs §Charts:
 * - primary series = accent, comparison = a second shade / gray (max 4);
 * - lines for rates, bars for discrete; gaps render as gaps;
 * - ~4 horizontal gridlines, no vertical gridlines, no axis box, zero baseline,
 *   percentage axis fixed 0-100;
 * - three layers: context label, summary-stat title, takeaway caption;
 * - scrub crosshair + single annotation, non-selected data dimmed;
 * - no gradients, no glow, area fill ≤8%.
 */

export interface Series {
  name: string;
  points: ChartPoint[];
  kind?: 'line' | 'bar';
  color?: string;
  fill?: boolean;
}

interface Props {
  series: Series[];
  height?: number;
  percentage?: boolean;
  /** Include zero in the y-axis (counts/rates/bars). Set false for dBm-style
   *  level metrics so the axis frames the data window instead of the floor.
   *  Forced true whenever any series is a bar (a truncated bar lies about size). */
  zeroBaseline?: boolean;
  /** A fixed, meaning-anchored y-domain (e.g. RSSI −90..−30 dBm). Pins the axis
   *  so a value reads identically across windows and never floats to the data max. */
  domain?: { min: number; max: number };
  /** A horizontal threshold reference (e.g. −70 dBm "poor"); drawn dashed with a
   *  small label. Licensed because the line IS a severity threshold. */
  reference?: { value: number; label?: string };
  yUnit?: string;
  contextLabel: string;
  /** The summary statistic shown as the chart title (e.g. "p95 42 ms"). */
  summaryStat?: string;
  /** Epoch seconds of the latest datum. Renders an honest "as of HH:MM:SS" stamp
   *  so a line that simply ends is never read as live. */
  asOf?: number | null;
  /** Precomputed staleness (latest datum older than the expected cadence, judged
   *  against the SERVER's fetch clock — kept out of render to stay pure/skew-free).
   *  Adds a "· stale" marker to the "as of" stamp. */
  stale?: boolean;
  takeaway?: string;
  /** Severity band — ONLY when the data itself is severity (threshold/anomaly). */
  band?: { from: number; to: number; color?: string; label?: string };
  className?: string;
}

// Two shades of one hue, then gray — never a rainbow (docs §Charts).
const PALETTE = ['var(--accent)', 'var(--accent-muted)', 'var(--fg-subtle)', 'var(--strong)'];

function useMeasuredWidth(fallback = 640): [RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect.width;
      if (cw && cw > 0) setW(Math.round(cw));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function TimeSeriesChart({
  series,
  height = 220,
  percentage = false,
  zeroBaseline = true,
  domain,
  reference,
  yUnit,
  contextLabel,
  summaryStat,
  asOf,
  stale = false,
  takeaway,
  band,
  className,
}: Props) {
  const [ref, width] = useMeasuredWidth();
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const M = { top: 8, right: 12, bottom: 20, left: 40 };
  const plotW = Math.max(10, width - M.left - M.right);
  const plotH = Math.max(10, height - M.top - M.bottom);

  // A bar can never have a truncated baseline (never-do rule 9), so any bar
  // series forces zero into the axis regardless of the caller's flag.
  const hasBar = series.some((s) => (s.kind ?? 'line') === 'bar');
  const effZeroBaseline = zeroBaseline || hasBar;

  const allValues: number[] = [];
  let tMin = Infinity;
  let tMax = -Infinity;
  for (const s of series) {
    for (const p of s.points) {
      if (p.ts < tMin) tMin = p.ts;
      if (p.ts > tMax) tMax = p.ts;
      if (p.value != null && Number.isFinite(p.value)) allValues.push(p.value);
      // Frame the axis around the full min/max envelope too, so a spread band is
      // never clipped by an axis fitted to the averages alone.
      if (p.min != null && Number.isFinite(p.min)) allValues.push(p.min);
      if (p.max != null && Number.isFinite(p.max)) allValues.push(p.max);
    }
  }
  const hasData = allValues.length > 0 && Number.isFinite(tMin) && Number.isFinite(tMax);

  // A window with a single distinct timestamp (one real bucket, or several
  // samples sharing a ts) has no time span to spread across. Collapse it to one
  // centred column so the lone sample and its label don't stack at the far edge.
  const tSpan = hasData ? tMax - tMin : 0;
  const singleColumn = hasData && tSpan <= 0;

  // A window spanning more than a day must date its ticks, or a 7-day trend reads
  // as a single day (and appears to run backwards when the clock wraps).
  const multiDay = tSpan > 86_400;
  const xLabel = multiDay ? dayLabel : clockLabel;

  const yScaleInfo = niceYScale(allValues, {
    percentage,
    zeroBaseline: effZeroBaseline,
    domain,
  });
  const xScale = linScale(tMin, tSpan > 0 ? tMax : tMin + 1, M.left, M.left + plotW);
  const x: Scale = singleColumn ? () => M.left + plotW / 2 : xScale;
  const y = linScale(yScaleInfo.min, yScaleInfo.max, M.top + plotH, M.top);

  // Scrub reference points = the longest series (drives the crosshair index).
  const refPoints = series.reduce<ChartPoint[]>(
    (best, s) => (s.points.length > best.length ? s.points : best),
    [],
  );

  function onMove(e: ReactMouseEvent) {
    const svg = svgRef.current;
    if (!svg || !refPoints.length) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width;
    setHover(nearestIndex(refPoints, x, px));
  }

  const hoverTs =
    hover != null && refPoints[hover] ? refPoints[hover].ts : null;
  const dim = hover != null;

  // Honest staleness: label the latest datum's wall-clock time; `stale` (computed
  // by the caller from the server's fetch clock) marks a line that just ends
  // because ingestion stalled, so it is never read as live (never-do rule 8).
  const isStale = stale && asOf != null;
  const asOfLabel = asOf != null ? `as of ${clockLabelSec(asOf)}` : null;

  // Fill and spread band only make sense against a real, meaningful baseline. A
  // dBm/level axis (no zero baseline, no fixed domain floor of meaning) must not
  // be area-filled to its padded floor — that fabricates magnitude (rule 11).
  const fillOk = effZeroBaseline || percentage;
  const areaBaseY = y(Math.max(0, yScaleInfo.min));

  return (
    <figure className={cn('m-0', className)}>
      {/* Layer 1 + 2: context label, then summary statistic as the title. */}
      <figcaption className="mb-2">
        <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {contextLabel}
          {yUnit ? <span className="tnum"> ({yUnit})</span> : null}
        </div>
        {summaryStat && (
          <div className="t-section tnum" style={{ color: 'var(--fg)' }}>
            {summaryStat}
          </div>
        )}
        {asOfLabel && (
          <div
            className="t-micro tnum"
            style={{ color: isStale ? 'var(--sev-p3)' : 'var(--fg-subtle)' }}
          >
            {asOfLabel}
            {isStale ? ' · stale' : ''}
          </div>
        )}
      </figcaption>

      <div ref={ref} className="w-full">
        <svg
          ref={svgRef}
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${contextLabel}${summaryStat ? `: ${summaryStat}` : ''}`}
          onMouseMove={hasData ? onMove : undefined}
          onMouseLeave={() => setHover(null)}
          style={{ display: 'block' }}
        >
          {/* Horizontal gridlines only (~4), no box, no vertical grid. */}
          {yScaleInfo.ticks.map((t) => {
            const py = y(t);
            return (
              <g key={t}>
                <line
                  x1={M.left}
                  x2={M.left + plotW}
                  y1={py}
                  y2={py}
                  stroke="var(--grid)"
                  strokeWidth={1}
                  shapeRendering="crispEdges"
                />
                <text
                  x={M.left - 6}
                  y={py}
                  textAnchor="end"
                  dominantBaseline="middle"
                  className="tnum"
                  fontSize={11}
                  fill="var(--fg-subtle)"
                >
                  {fmt(t, percentage ? 0 : Math.abs(t) < 10 ? 1 : 0)}
                </text>
              </g>
            );
          })}

          {/* Severity band (only when data is severity). */}
          {hasData && band && (
            <rect
              x={M.left}
              width={plotW}
              y={y(band.to)}
              height={Math.max(0, y(band.from) - y(band.to))}
              fill={band.color ?? 'var(--sev-p2)'}
              opacity={0.08}
            />
          )}

          {/* Threshold reference line (the line itself IS the severity). */}
          {hasData &&
            reference &&
            reference.value >= yScaleInfo.min &&
            reference.value <= yScaleInfo.max && (
              <g>
                <line
                  x1={M.left}
                  x2={M.left + plotW}
                  y1={y(reference.value)}
                  y2={y(reference.value)}
                  stroke="var(--fg-subtle)"
                  strokeWidth={1}
                  strokeDasharray="4 3"
                  opacity={0.8}
                />
                {reference.label && (
                  <text
                    x={M.left + plotW}
                    y={y(reference.value) - 3}
                    textAnchor="end"
                    className="tnum"
                    fontSize={10}
                    fill="var(--fg-subtle)"
                  >
                    {reference.label}
                  </text>
                )}
              </g>
            )}

          {/* Series. */}
          {hasData &&
            series.map((s, si) => {
              const color = s.color ?? PALETTE[si % PALETTE.length];
              const opacity = dim ? 0.45 : 1;
              if ((s.kind ?? 'line') === 'bar') {
                const slot = plotW / Math.max(1, s.points.length);
                const bw = Math.max(1, slot * 0.65);
                const base = y(Math.max(0, yScaleInfo.min));
                return (
                  <g key={s.name} opacity={opacity}>
                    {s.points.map((p, i) => {
                      if (p.value == null || !Number.isFinite(p.value)) return null;
                      const cx = x(p.ts);
                      const top = y(p.value);
                      return (
                        <rect
                          key={i}
                          x={cx - bw / 2}
                          y={Math.min(top, base)}
                          width={bw}
                          height={Math.max(1, Math.abs(base - top))}
                          fill={color}
                        />
                      );
                    })}
                  </g>
                );
              }
              const d = linePath(s.points, x, y);
              // Spread band (rollup min/max) behind the line; when it carries
              // real spread we skip the baseline fill to avoid a doubled wash.
              const bands = bandPaths(s.points, x, y);
              const areas =
                s.fill && fillOk && bands.length === 0
                  ? areaPaths(s.points, x, y, areaBaseY)
                  : [];
              const dots = isolatedPoints(s.points);
              return (
                <g key={s.name} opacity={opacity}>
                  {bands.map((b, i) => (
                    <path key={`band-${i}`} d={b} fill={color} opacity={0.08} stroke="none" />
                  ))}
                  {areas.map((a, i) => (
                    <path key={`area-${i}`} d={a} fill={color} opacity={0.08} stroke="none" />
                  ))}
                  <path
                    d={d}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.75}
                    strokeLinejoin="round"
                    vectorEffect="non-scaling-stroke"
                  />
                  {dots.map((p, i) => (
                    <circle
                      key={`dot-${i}`}
                      cx={x(p.ts)}
                      cy={y(p.value as number)}
                      r={2.5}
                      fill={color}
                    />
                  ))}
                </g>
              );
            })}

          {/* Scrub crosshair + emphasized points on the selected column. */}
          {hasData && hover != null && hoverTs != null && (
            <g>
              <line
                x1={x(hoverTs)}
                x2={x(hoverTs)}
                y1={M.top}
                y2={M.top + plotH}
                stroke="var(--fg-subtle)"
                strokeWidth={1}
                strokeDasharray="3 3"
              />
              {series.map((s, si) => {
                const p = s.points[hover];
                if (!p || p.value == null || !Number.isFinite(p.value)) return null;
                const color = s.color ?? PALETTE[si % PALETTE.length];
                return (
                  <circle key={s.name} cx={x(p.ts)} cy={y(p.value)} r={3} fill={color} />
                );
              })}
            </g>
          )}

          {/* X labels: one centred label for a single-column window, else
              first / middle / last. */}
          {hasData && singleColumn && (
            <text
              x={M.left + plotW / 2}
              y={height - 6}
              textAnchor="middle"
              className="tnum"
              fontSize={11}
              fill="var(--fg-subtle)"
            >
              {xLabel(tMin)}
            </text>
          )}
          {hasData &&
            !singleColumn &&
            [tMin, (tMin + tMax) / 2, tMax].map((t, i) => (
              <text
                key={i}
                x={x(t)}
                y={height - 6}
                textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                className="tnum"
                fontSize={11}
                fill="var(--fg-subtle)"
              >
                {xLabel(t)}
              </text>
            ))}

          {!hasData && (
            <text
              x={M.left + plotW / 2}
              y={M.top + plotH / 2}
              textAnchor="middle"
              dominantBaseline="middle"
              fontSize={13}
              fill="var(--fg-subtle)"
            >
              No data in this window
            </text>
          )}
        </svg>
      </div>

      {/* Single annotation for the scrubbed column (value + timestamp). */}
      {hasData && hover != null && hoverTs != null && (
        <ScrubAnnotation series={series} index={hover} ts={hoverTs} unit={yUnit} multiDay={multiDay} />
      )}

      {/* Layer 3: takeaway caption. */}
      {takeaway && !dim && (
        <figcaption className="t-caption mt-1" style={{ color: 'var(--fg-muted)' }}>
          {takeaway}
        </figcaption>
      )}
    </figure>
  );
}

function ScrubAnnotation({
  series,
  index,
  ts,
  unit,
  multiDay = false,
}: {
  series: Series[];
  index: number;
  ts: number;
  unit?: string;
  multiDay?: boolean;
}) {
  return (
    <div
      className="mt-1 t-caption flex flex-wrap items-center gap-x-3 gap-y-0.5"
      style={{ color: 'var(--fg-muted)' }}
    >
      <span className="tnum" style={{ color: 'var(--fg)' }}>
        {multiDay ? dayTimeLabel(ts) : clockLabel(ts)}
      </span>
      {series.map((s, si) => {
        const p = s.points[index];
        const v = p?.value;
        const color = s.color ?? PALETTE[si % PALETTE.length];
        return (
          <span key={s.name} className="inline-flex items-center gap-1">
            <span
              aria-hidden
              className="inline-block w-2 h-0.5 rounded-full"
              style={{ background: color }}
            />
            {s.name}:{' '}
            <span className="tnum" style={{ color: 'var(--fg)' }}>
              {v == null || !Number.isFinite(v) ? 'gap' : fmt(v, 1)}
              {unit && v != null ? ` ${unit}` : ''}
            </span>
          </span>
        );
      })}
    </div>
  );
}
