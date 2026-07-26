import { fmt, linScale, niceYScale } from '../../../components/ui/chart-utils';
import { cn } from '../../../components/ui/cn';
import type { RssiBin } from '../model';
import { useMeasuredWidth } from './useMeasuredWidth';

/**
 * Client-RSSI distribution histogram (docs/REPORT_SPEC.md §Client analysis):
 * never an average without its distribution. Contiguous bins on a count axis with
 * a true zero baseline; the weak tail (bins the backend flags `weak`) is coloured
 * with the caution ramp so the bad corner an average would hide is visible. The
 * neutral accent carries the healthy bulk. Hand-rolled SVG, no chart library.
 */

interface Props {
  bins: RssiBin[];
  contextLabel: string;
  summaryStat?: string;
  takeaway?: string;
  height?: number;
  className?: string;
}

export function Histogram({
  bins,
  contextLabel,
  summaryStat,
  takeaway,
  height = 200,
  className,
}: Props) {
  const [ref, width] = useMeasuredWidth();
  const counts = bins.map((b) => b.count);
  const hasData = bins.length > 0 && counts.some((c) => c > 0);

  const M = { top: 12, right: 8, bottom: 34, left: 34 };
  const plotW = Math.max(10, width - M.left - M.right);
  const plotH = Math.max(10, height - M.top - M.bottom);
  const scale = niceYScale(counts, { zeroBaseline: true });
  const y = linScale(scale.min, scale.max, M.top + plotH, M.top);
  const base = y(0);
  const slot = plotW / Math.max(1, bins.length);
  const gap = 1.5;
  const bw = Math.max(1, slot - gap);

  return (
    <figure className={cn('m-0', className)}>
      <figcaption className="mb-2">
        <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {contextLabel}
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
            <span className="t-caption">No client RSSI collected in this window</span>
          </div>
        ) : (
          <svg
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={contextLabel}
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

            {bins.map((b, i) => {
              const cx = M.left + slot * i + slot / 2;
              const top = y(b.count);
              return (
                <g key={i}>
                  <rect
                    x={cx - bw / 2}
                    y={Math.min(top, base)}
                    width={bw}
                    height={Math.max(b.count > 0 ? 1 : 0, Math.abs(base - top))}
                    fill={b.weak ? 'var(--sev-p2)' : 'var(--accent)'}
                  />
                </g>
              );
            })}

            {/* Bin labels: thinned so a dense axis stays legible. */}
            {bins.map((b, i) => {
              const showEvery = bins.length > 10 ? 2 : 1;
              if (i % showEvery !== 0 && i !== bins.length - 1) return null;
              return (
                <text
                  key={`lbl-${i}`}
                  x={M.left + slot * i + slot / 2}
                  y={height - 16}
                  textAnchor="middle"
                  className="tnum"
                  fontSize={10}
                  fill="var(--fg-subtle)"
                >
                  {b.label}
                </text>
              );
            })}
            <text
              x={M.left + plotW / 2}
              y={height - 2}
              textAnchor="middle"
              className="tnum"
              fontSize={10}
              fill="var(--fg-subtle)"
            >
              RSSI (dBm)
            </text>
          </svg>
        )}
      </div>

      {hasData && (
        <div className="flex items-center gap-4 mt-1.5 t-caption" style={{ color: 'var(--fg-muted)' }}>
          <LegendSwatch color="var(--accent)" label="Usable signal" />
          <LegendSwatch color="var(--sev-p2)" label="Weak tail (poor coverage)" />
        </div>
      )}

      {takeaway && (
        <figcaption className="t-caption mt-1" style={{ color: 'var(--fg-muted)' }}>
          {takeaway}
        </figcaption>
      )}
    </figure>
  );
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className="inline-block w-3 h-2 rounded-[1px]"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}
