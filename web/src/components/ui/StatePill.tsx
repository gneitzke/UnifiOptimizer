import { Check, CircleDashed } from 'lucide-react';
import type { IssueState, Severity } from '../../api/types';
import { cn } from './cn';

/**
 * Lifecycle pill (docs §Severity & lifecycle presentation):
 * - pending   → neutral gray OUTLINE (not yet real)
 * - active    → severity-tinted (the only severity fills in the UI besides P1)
 * - resolving → tinted + a STATIC open glyph (CircleDashed), no motion. Resolving
 *   is a passive countdown toward K clean checks — nothing runs between checks,
 *   and on an intermittent fault the count never completes. A spinner claimed
 *   activity that isn't happening and made a list of them read as stuck
 *   (Gitea #39, #49). The dashed unclosed circle is the "loop not closed yet"
 *   metaphor one step before resolved's solid check; the fraction is the sole
 *   progress affordance and lives in text ("Resolving · 3 of 6 clean checks").
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
  /** Clear-streak progress ("3 of 6 clean checks"), appended for the resolving
   * state only. Ignored elsewhere — no other state is counting towards
   * anything. Passed in rather than derived here so this stays a presentation
   * primitive with no knowledge of the issue payload. */
  progress?: string | null;
  /** Hover text; the caller supplies the sentence behind `progress`. */
  title?: string;
  className?: string;
}

export function StatePill({ state, severity, label, progress, title, className }: Props) {
  const sev = severity ? SEV_COLOR[severity] : null;
  const stateText = label ?? LABEL[state];
  const text =
    state === 'resolving' && progress ? `${stateText} · ${progress}` : stateText;

  const base =
    'inline-flex items-center gap-1 h-[20px] px-1.5 rounded-full text-[12px] font-medium whitespace-nowrap';

  if (state === 'pending') {
    return (
      <span
        className={cn(base, 'border', className)}
        style={{ borderColor: 'var(--strong)', color: 'var(--fg-subtle)' }}
        title={title}
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
        title={title}
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
    <span className={cn(base, className)} style={{ background: fill, color }} title={title}>
      {state === 'resolving' && (
        <CircleDashed size={12} strokeWidth={2.5} aria-hidden />
      )}
      {text}
    </span>
  );
}
