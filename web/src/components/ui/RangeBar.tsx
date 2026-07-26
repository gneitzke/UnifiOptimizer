import { fmt } from './chart-utils';
import { cn } from './cn';

/**
 * Horizontal range bar (Apple Health pattern, docs §Charts / §Pattern
 * references): a min–max span on a non-truncated, zero-based track, with an
 * optional current-value marker. Labels are tabular. No gradient, no glow.
 */

interface Props {
  min: number;
  max: number;
  /** Axis extent; defaults to a zero-based domain covering the data. */
  domainMin?: number;
  domainMax?: number;
  /** Optional current-value marker within the span. */
  value?: number;
  color?: string;
  label?: string;
  unit?: string;
  percentage?: boolean;
  className?: string;
}

export function RangeBar({
  min,
  max,
  domainMin,
  domainMax,
  value,
  color = 'var(--accent)',
  label,
  unit,
  percentage = false,
  className,
}: Props) {
  const dMin = percentage ? 0 : (domainMin ?? 0);
  const autoMax = Math.max(max, value ?? max) * 1.1;
  const dMax = percentage ? 100 : (domainMax ?? autoMax) || 1;
  const span = dMax - dMin || 1;

  const pct = (v: number) => `${(((v - dMin) / span) * 100).toFixed(2)}%`;
  const leftPct = ((min - dMin) / span) * 100;
  const widthPct = ((max - min) / span) * 100;

  return (
    <div className={cn('w-full', className)}>
      {(label || unit) && (
        <div className="flex items-baseline justify-between mb-1">
          {label && (
            <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
              {label}
            </span>
          )}
          <span className="t-caption tnum" style={{ color: 'var(--fg)' }}>
            {fmt(min, 1)}–{fmt(max, 1)}
            {unit ? ` ${unit}` : ''}
          </span>
        </div>
      )}

      <div
        className="relative h-2 rounded-full"
        style={{ background: 'var(--hairline)' }}
        role="img"
        aria-label={`range ${fmt(min, 1)} to ${fmt(max, 1)}${unit ? ' ' + unit : ''}`}
      >
        <div
          className="absolute top-0 h-2 rounded-full"
          style={{
            left: `${Math.max(0, leftPct)}%`,
            width: `${Math.max(1, Math.min(100 - leftPct, widthPct))}%`,
            background: color,
          }}
        />
        {value != null && Number.isFinite(value) && (
          <div
            className="absolute top-[-2px] h-3 w-[2px] rounded-full"
            style={{ left: pct(value), background: 'var(--fg)' }}
            title={`${fmt(value, 1)}${unit ? ' ' + unit : ''}`}
          />
        )}
      </div>

      <div className="flex justify-between mt-1 t-micro" style={{ color: 'var(--fg-subtle)' }}>
        <span className="tnum">{fmt(dMin, 0)}</span>
        <span className="tnum">{fmt(dMax, 0)}</span>
      </div>
    </div>
  );
}
