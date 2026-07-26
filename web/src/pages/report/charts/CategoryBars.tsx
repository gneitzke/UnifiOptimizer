import { fmt, linScale, niceYScale } from '../../../components/ui/chart-utils';
import { cn } from '../../../components/ui/cn';
import { useMeasuredWidth } from './useMeasuredWidth';

/**
 * Categorical bar chart (docs/DESIGN_FOUNDATION.md §Charts) for discrete, gappy
 * data: per-SLE scores, channel utilisation, neighbour density, clients-per-AP.
 * Hand-rolled SVG, no chart library. Bars sit on a true zero baseline on a
 * non-truncated axis; ~4 gridlines, no gridline walls, one accent + the status
 * ramp, direct value labels, optional dashed threshold reference. Three layers:
 * a context label, a summary-stat title, and an optional takeaway.
 */

export interface CategoryBar {
  label: string;
  value: number | null;
  /** Optional per-bar colour override (severity/status ramp), else the accent. */
  color?: string;
}

interface Props {
  data: CategoryBar[];
  orientation?: 'vertical' | 'horizontal';
  /** Lock the value axis to 0-100 (scores, utilisation %). */
  percentage?: boolean;
  /** Fixed value-axis max when not a percentage (keeps a row of charts comparable). */
  domainMax?: number;
  reference?: { value: number; label?: string };
  unit?: string;
  contextLabel: string;
  summaryStat?: string;
  takeaway?: string;
  /** Bar colour when a bar has no per-bar override. */
  color?: string;
  height?: number;
  /** Show the numeric value at the end of each bar. */
  showValues?: boolean;
  className?: string;
}

export function CategoryBars({
  data,
  orientation = 'vertical',
  percentage = false,
  domainMax,
  reference,
  unit,
  contextLabel,
  summaryStat,
  takeaway,
  color = 'var(--accent)',
  height,
  showValues = true,
  className,
}: Props) {
  const [ref, width] = useMeasuredWidth();
  const values = data
    .map((d) => d.value)
    .filter((v): v is number => v != null && Number.isFinite(v));
  const hasData = data.length > 0 && values.length > 0;

  const scale = niceYScale(domainMax != null ? [0, domainMax] : values, {
    percentage,
    zeroBaseline: true,
  });

  const digits = percentage ? 0 : Math.max(...values.map((v) => Math.abs(v)), 0) < 10 ? 1 : 0;

  return (
    <figure className={cn('m-0', className)}>
      <figcaption className="mb-2">
        <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {contextLabel}
          {unit ? <span className="tnum"> ({unit})</span> : null}
        </div>
        {summaryStat && (
          <div className="t-section tnum" style={{ color: 'var(--fg)' }}>
            {summaryStat}
          </div>
        )}
      </figcaption>

      <div ref={ref} className="w-full">
        {!hasData ? (
          <div
            className="flex items-center justify-center rounded-card"
            style={{ height: 96, border: '1px dashed var(--hairline)', color: 'var(--fg-subtle)' }}
          >
            <span className="t-caption">No data in this window</span>
          </div>
        ) : orientation === 'horizontal' ? (
          <HorizontalBars
            data={data}
            width={width}
            scale={scale}
            reference={reference}
            color={color}
            unit={unit}
            digits={digits}
            showValues={showValues}
          />
        ) : (
          <VerticalBars
            data={data}
            width={width}
            height={height ?? 200}
            scale={scale}
            reference={reference}
            color={color}
            digits={digits}
            showValues={showValues}
          />
        )}
      </div>

      {takeaway && (
        <figcaption className="t-caption mt-1" style={{ color: 'var(--fg-muted)' }}>
          {takeaway}
        </figcaption>
      )}
    </figure>
  );
}

function VerticalBars({
  data,
  width,
  height,
  scale,
  reference,
  color,
  digits,
  showValues,
}: {
  data: CategoryBar[];
  width: number;
  height: number;
  scale: { min: number; max: number; ticks: number[] };
  reference?: { value: number; label?: string };
  color: string;
  digits: number;
  showValues: boolean;
}) {
  const M = { top: 14, right: 8, bottom: 26, left: 34 };
  const plotW = Math.max(10, width - M.left - M.right);
  const plotH = Math.max(10, height - M.top - M.bottom);
  const y = linScale(scale.min, scale.max, M.top + plotH, M.top);
  const base = y(0);
  const slot = plotW / data.length;
  const bw = Math.max(2, Math.min(slot * 0.62, 40));

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      style={{ display: 'block' }}
    >
      {scale.ticks.map((t) => {
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
              {fmt(t, 0)}
            </text>
          </g>
        );
      })}

      {reference != null &&
        reference.value >= scale.min &&
        reference.value <= scale.max && (
          <g>
            <line
              x1={M.left}
              x2={M.left + plotW}
              y1={y(reference.value)}
              y2={y(reference.value)}
              stroke="var(--sev-p2)"
              strokeWidth={1}
              strokeDasharray="4 3"
              opacity={0.85}
            />
            {reference.label && (
              <text
                x={M.left + 2}
                y={y(reference.value) - 3}
                textAnchor="start"
                className="tnum"
                fontSize={10}
                fill="var(--sev-p2)"
              >
                {reference.label}
              </text>
            )}
          </g>
        )}

      {data.map((d, i) => {
        const cx = M.left + slot * (i + 0.5);
        if (d.value == null || !Number.isFinite(d.value)) {
          return (
            <text
              key={i}
              x={cx}
              y={base - 4}
              textAnchor="middle"
              fontSize={10}
              fill="var(--fg-subtle)"
            >
              —
            </text>
          );
        }
        const top = y(d.value);
        return (
          <g key={i}>
            <rect
              x={cx - bw / 2}
              y={Math.min(top, base)}
              width={bw}
              height={Math.max(1, Math.abs(base - top))}
              fill={d.color ?? color}
            />
            {showValues && (
              <text
                x={cx}
                y={top - 4}
                textAnchor="middle"
                className="tnum"
                fontSize={10}
                fill="var(--fg-muted)"
              >
                {fmt(d.value, digits)}
              </text>
            )}
          </g>
        );
      })}

      {data.map((d, i) => (
        <text
          key={`lbl-${i}`}
          x={M.left + slot * (i + 0.5)}
          y={height - 8}
          textAnchor="middle"
          className="tnum"
          fontSize={11}
          fill="var(--fg-subtle)"
        >
          {d.label}
        </text>
      ))}
    </svg>
  );
}

function HorizontalBars({
  data,
  width,
  scale,
  reference,
  color,
  unit,
  digits,
  showValues,
}: {
  data: CategoryBar[];
  width: number;
  scale: { min: number; max: number; ticks: number[] };
  reference?: { value: number; label?: string };
  color: string;
  unit?: string;
  digits: number;
  showValues: boolean;
}) {
  const rowH = 26;
  const labelW = Math.min(160, Math.max(90, Math.round(width * 0.32)));
  const valueW = showValues ? 52 : 8;
  const M = { top: 4, right: valueW, bottom: 4, left: labelW };
  const plotW = Math.max(10, width - M.left - M.right);
  const height = M.top + M.bottom + data.length * rowH;
  const x = linScale(scale.min, scale.max, M.left, M.left + plotW);
  const bh = 12;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      style={{ display: 'block' }}
    >
      {reference != null &&
        reference.value >= scale.min &&
        reference.value <= scale.max && (
          <line
            x1={x(reference.value)}
            x2={x(reference.value)}
            y1={M.top}
            y2={height - M.bottom}
            stroke="var(--sev-p2)"
            strokeWidth={1}
            strokeDasharray="4 3"
            opacity={0.85}
          />
        )}

      {data.map((d, i) => {
        const cy = M.top + rowH * i + rowH / 2;
        const val = d.value != null && Number.isFinite(d.value) ? d.value : null;
        const bx = val != null ? x(val) : M.left;
        return (
          <g key={i}>
            <text
              x={M.left - 8}
              y={cy}
              textAnchor="end"
              dominantBaseline="middle"
              className="tnum"
              fontSize={11}
              fill="var(--fg-muted)"
            >
              {d.label}
            </text>
            <line
              x1={M.left}
              x2={M.left + plotW}
              y1={cy}
              y2={cy}
              stroke="var(--grid)"
              strokeWidth={1}
              shapeRendering="crispEdges"
              opacity={0.5}
            />
            {val != null && (
              <rect
                x={M.left}
                y={cy - bh / 2}
                width={Math.max(1, bx - M.left)}
                height={bh}
                fill={d.color ?? color}
                rx={1}
              />
            )}
            {showValues && (
              <text
                x={width - 4}
                y={cy}
                textAnchor="end"
                dominantBaseline="middle"
                className="tnum"
                fontSize={11}
                fill="var(--fg)"
              >
                {val == null ? '—' : `${fmt(val, digits)}${unit ? ` ${unit}` : ''}`}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
