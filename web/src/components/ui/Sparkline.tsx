import type { ChartPoint } from './chart-utils';
import { areaPaths, linePath, linScale } from './chart-utils';

/**
 * Minimal trend glyph (docs §Charts). Line for rates, bars for discrete data;
 * gaps stay gaps. No axes, no glow, no gradient; optional area fill ≤8%. Used in
 * MetricTile and dense table cells so a number is never shown bare (rule 5).
 *
 * A same-metric ROW of glyphs (e.g. per-radio channel utilization) must share a
 * baseline or their shapes cannot be compared — pass `percentage` (locks 0-100)
 * or `domainMin`/`domainMax` so every tile is drawn on one scale. Left auto, each
 * glyph frames its own min/max and 4% would look as tall as 40%.
 */

interface Props {
  data: Array<number | null>;
  kind?: 'line' | 'bar';
  width?: number;
  height?: number;
  color?: string;
  /** Area fill under a line (≤8% opacity). */
  fill?: boolean;
  /** Lock the y-range to 0-100 so same-metric % glyphs share a baseline. */
  percentage?: boolean;
  /** Fixed y-range (shared across a same-metric row) when not a percentage. */
  domainMin?: number;
  domainMax?: number;
  className?: string;
  ariaLabel?: string;
}

/** Indices of finite samples with no finite neighbour either side — a lone
 *  reading a line can't connect; drawn as a dot so it doesn't vanish (or, with
 *  fill, become a full-width wedge). */
function isolatedIndices(data: Array<number | null>): number[] {
  const fin = (v: number | null | undefined) => v != null && Number.isFinite(v);
  const out: number[] = [];
  for (let i = 0; i < data.length; i++) {
    if (fin(data[i]) && !fin(data[i - 1]) && !fin(data[i + 1])) out.push(i);
  }
  return out;
}

export function Sparkline({
  data,
  kind = 'line',
  width = 96,
  height = 28,
  color = 'var(--accent)',
  fill = false,
  percentage = false,
  domainMin,
  domainMax,
  className,
  ariaLabel,
}: Props) {
  const pad = 2;
  const n = data.length;

  const finite = data.filter((v): v is number => v != null && Number.isFinite(v));
  if (n === 0 || finite.length === 0) {
    return <svg width={width} height={height} className={className} aria-hidden />;
  }

  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  let yLo: number;
  let yHi: number;
  if (percentage) {
    yLo = 0;
    yHi = 100;
  } else if (domainMin != null && domainMax != null) {
    yLo = domainMin;
    yHi = domainMax;
  } else {
    yLo = kind === 'bar' ? Math.min(0, lo) : lo;
    yHi = hi === yLo ? yLo + 1 : hi;
  }

  const x = linScale(0, Math.max(1, n - 1), pad, width - pad);
  const y = linScale(yLo, yHi, height - pad, pad);

  if (kind === 'bar') {
    const slot = (width - pad * 2) / n;
    const bw = Math.max(1, slot * 0.7);
    const base = y(Math.max(0, yLo));
    return (
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        className={className}
        role="img"
        aria-label={ariaLabel}
      >
        {data.map((v, i) => {
          if (v == null || !Number.isFinite(v)) return null; // gap
          const cx = pad + slot * (i + 0.5);
          const top = y(v);
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
      </svg>
    );
  }

  const points: ChartPoint[] = data.map((v, i) => ({ ts: i, value: v }));
  const d = linePath(points, x, y);
  // Per-run fill closed to the glyph floor — never one wedge across a gap.
  const areas = fill ? areaPaths(points, x, y, height - pad) : [];
  const dots = isolatedIndices(data);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label={ariaLabel}
    >
      {areas.map((a, i) => (
        <path key={`a-${i}`} d={a} fill={color} opacity={0.08} stroke="none" />
      ))}
      <path
        d={d}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {dots.map((i) => (
        <circle key={`d-${i}`} cx={x(i)} cy={y(data[i] as number)} r={1.6} fill={color} />
      ))}
    </svg>
  );
}
