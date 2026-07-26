import { Check, Loader2 } from 'lucide-react';
import type { IssueState, Severity } from '../../api/types';
import { cn } from './cn';

/**
 * Lifecycle pill (docs §Severity & lifecycle presentation):
 * - pending   → neutral gray OUTLINE (not yet real)
 * - active    → severity-tinted (the only severity fills in the UI besides P1)
 * - resolving → tinted + a progress affordance (spinner)
 * - resolved  → gray + checkmark, NEVER green (green is reserved for health, so
 *   real "healthy" signals stay meaningful)
 */

const SEV_COLOR: Record<Severity, { color: string; fill: string }> = {
  p1: { color: 'var(--sev-p1)', fill: 'var(--sev-p1-fill)' },
  p2: { color: 'var(--sev-p2)', fill: 'var(--sev-p2-fill)' },
  p3: { color: 'var(--sev-p3)', fill: 'var(--sev-p3-fill)' },
};

const LABEL: Record<IssueState, string> = {
  pending: 'Pending',
  active: 'Active',
  resolving: 'Resolving',
  resolved: 'Resolved',
};

interface Props {
  state: IssueState;
  /** Drives the tint for active/resolving; defaults to accent when absent. */
  severity?: Severity;
  label?: string;
  className?: string;
}

export function StatePill({ state, severity, label, className }: Props) {
  const sev = severity ? SEV_COLOR[severity] : null;
  const text = label ?? LABEL[state];

  const base =
    'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-full text-[12px] font-medium whitespace-nowrap';

  if (state === 'pending') {
    return (
      <span
        className={cn(base, 'border', className)}
        style={{ borderColor: 'var(--strong)', color: 'var(--fg-subtle)' }}
      >
        {text}
      </span>
    );
  }

  if (state === 'resolved') {
    return (
      <span
        className={cn(base, className)}
        style={{ background: 'var(--sev-neutral-fill)', color: 'var(--sev-neutral)' }}
      >
        <Check size={12} strokeWidth={2.5} />
        {text}
      </span>
    );
  }

  // active | resolving — severity tint (fall back to accent when no severity)
  const color = sev?.color ?? 'var(--accent)';
  const fill =
    sev?.fill ?? 'color-mix(in srgb, var(--accent) 12%, transparent)';

  return (
    <span className={cn(base, className)} style={{ background: fill, color }}>
      {state === 'resolving' && (
        <Loader2 size={12} strokeWidth={2.5} className="animate-spin" />
      )}
      {text}
    </span>
  );
}
