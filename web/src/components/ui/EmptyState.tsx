import type { ReactNode } from 'react';
import { Button } from './Button';
import { cn } from './cn';

/**
 * Empty states that distinguish three cases honestly (docs §Interaction):
 * - "no-data"  → nothing collected yet: setup guidance.
 * - "no-match" → filters exclude everything: offer to clear.
 * - "healthy"  → genuinely nothing wrong: stated plainly as a positive, ONE
 *   line, no illustration (green stays reserved for real health signals).
 */

type Variant = 'no-data' | 'no-match' | 'healthy';

interface Props {
  variant: Variant;
  title?: string;
  description?: ReactNode;
  /** Primary action (e.g. "Clear filters"). */
  action?: { label: string; onClick: () => void };
  icon?: ReactNode;
  className?: string;
}

const DEFAULT_TITLE: Record<Variant, string> = {
  'no-data': 'Nothing collected yet',
  'no-match': 'No matches',
  healthy: 'No active issues',
};

export function EmptyState({
  variant,
  title,
  description,
  action,
  icon,
  className,
}: Props) {
  // Healthy is a one-liner positive — no illustration, no icon.
  if (variant === 'healthy') {
    return (
      <div
        className={cn(
          'flex items-center justify-center gap-2 py-8 t-body',
          className,
        )}
        style={{ color: 'var(--sev-healthy)' }}
      >
        <span
          aria-hidden
          className="inline-block w-2 h-2 rounded-full"
          style={{ background: 'var(--sev-healthy)' }}
        />
        <span style={{ color: 'var(--fg)' }}>{title ?? DEFAULT_TITLE.healthy}</span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center py-12 px-6',
        className,
      )}
    >
      {icon && (
        <div className="mb-3" style={{ color: 'var(--fg-subtle)' }} aria-hidden>
          {icon}
        </div>
      )}
      <div className="t-section" style={{ color: 'var(--fg)' }}>
        {title ?? DEFAULT_TITLE[variant]}
      </div>
      {description && (
        <div className="mt-1 t-secondary max-w-sm" style={{ color: 'var(--fg-muted)' }}>
          {description}
        </div>
      )}
      {action && (
        <Button variant="secondary" size="sm" className="mt-4" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
