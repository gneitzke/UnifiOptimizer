import { cn } from './cn';

/**
 * Quiet count badge for the sidebar (docs §Interaction: "quiet gray count
 * badges, red only when P1s exist"). Gray by default; when `alert`, it adopts
 * the P1 red treatment as a tint (color encodes meaning, stays quiet).
 */

interface Props {
  count: number;
  alert?: boolean;
  className?: string;
  title?: string;
}

export function CountBadge({ count, alert = false, className, title }: Props) {
  if (!count) return null;
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5',
        'rounded-full text-[11px] font-medium tnum',
        className,
      )}
      style={{
        background: alert ? 'var(--sev-p1-fill)' : 'var(--sev-neutral-fill)',
        color: alert ? 'var(--sev-p1)' : 'var(--fg-subtle)',
      }}
    >
      {count > 99 ? '99+' : count}
    </span>
  );
}
