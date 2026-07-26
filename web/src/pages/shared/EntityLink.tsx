import { Link } from 'react-router-dom';
import { cn } from '../../components/ui/cn';
import { entityHref, entityLabel, type EntityRef } from './api';

/**
 * Renders an entity reference as a link to its detail page when it has one
 * (device / client), or as plain text otherwise (ports, radios, WLANs have no
 * own route yet). The name is resolved server-side; we fall back to the native
 * id, then the numeric id — never a bare number presented as a name.
 *
 * INTEGRATE NOTE: candidate to promote into `src/components/ui`.
 */
export function EntityLink({
  entity,
  className,
  muted = false,
}: {
  entity: EntityRef | null | undefined;
  className?: string;
  muted?: boolean;
}) {
  const label = entityLabel(entity);
  const href = entityHref(entity);
  const color = muted ? 'var(--fg-muted)' : 'var(--fg)';

  if (!entity) {
    return (
      <span className={cn('t-body', className)} style={{ color: 'var(--fg-subtle)' }}>
        —
      </span>
    );
  }

  if (!href) {
    return (
      <span className={cn('t-body', className)} style={{ color }} title={entity.native_id ?? undefined}>
        {label}
      </span>
    );
  }

  return (
    <Link
      to={href}
      className={cn('t-body hover:underline', className)}
      style={{ color: muted ? 'var(--fg-muted)' : 'var(--accent)' }}
      title={entity.native_id ?? undefined}
      onClick={(e) => e.stopPropagation()}
    >
      {label}
    </Link>
  );
}
