import { ArrowDown, ArrowUp } from 'lucide-react';
import { Card } from './Card';
import { Sparkline } from './Sparkline';
import { RelativeTime } from './RelativeTime';
import { fmt } from './chart-utils';
import { cn } from './cn';

/**
 * A metric with context, never a bare number (never-do rule 5): label + value +
 * sparkline + optional delta. The delta pairs a direction arrow (shape) with an
 * optional tone (color encodes meaning only when `goodDirection` is given).
 */

interface Props {
  label: string;
  value: number | string | null;
  unit?: string;
  digits?: number;
  spark?: Array<number | null>;
  sparkKind?: 'line' | 'bar';
  delta?: number;
  deltaUnit?: string;
  /** Which direction is an improvement; enables green/amber tone. */
  goodDirection?: 'up' | 'down';
  /** Epoch seconds of the underlying sample, labeled "as of HH:MM:SS". */
  asOf?: number | null;
  className?: string;
}

export function MetricTile({
  label,
  value,
  unit,
  digits = 0,
  spark,
  sparkKind = 'line',
  delta,
  deltaUnit,
  goodDirection,
  asOf,
  className,
}: Props) {
  const display =
    value == null ? '—' : typeof value === 'number' ? fmt(value, digits) : value;

  let deltaColor = 'var(--fg-muted)';
  if (delta != null && delta !== 0 && goodDirection) {
    const up = delta > 0;
    const good = (up ? 'up' : 'down') === goodDirection;
    deltaColor = good ? 'var(--sev-healthy)' : 'var(--sev-p2)';
  }

  return (
    <Card pad="sm" className={cn('flex flex-col gap-2', className)}>
      <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
        {label}
      </div>

      <div className="flex items-baseline gap-1.5">
        <span className="t-metric" style={{ color: 'var(--fg)' }}>
          {display}
        </span>
        {unit && (
          <span className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
            {unit}
          </span>
        )}
        {delta != null && delta !== 0 && (
          <span
            className="inline-flex items-center gap-0.5 t-caption tnum ml-1"
            style={{ color: deltaColor }}
          >
            {delta > 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
            {fmt(Math.abs(delta), 1)}
            {deltaUnit ?? unit ?? ''}
          </span>
        )}
      </div>

      {spark && spark.length > 0 && (
        <Sparkline data={spark} kind={sparkKind} width={140} height={30} ariaLabel={`${label} trend`} />
      )}

      {asOf != null && (
        <div className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
          <RelativeTime ts={asOf} mode="as-of" />
        </div>
      )}
    </Card>
  );
}
