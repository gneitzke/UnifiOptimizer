import { Link } from 'react-router-dom';
import { cn } from '../../components/ui/cn';
import { entityHref, entityLabel, type EntityRef } from './api';

/**
 * Renders an entity reference as a link to wherever that entity is actually
 * shown: its own detail page for a device or a client, and the parent device's
 * page for a radio or a port (see `entityHref` — they have no route of their
 * own, but they are shown on the device page, so linking there is the honest
 * destination rather than leaving them dead text next to linked siblings). Only
 * a WLAN, which has neither a page nor a parent device, stays plain text.
 *
 * The name is resolved server-side; we fall back to the native id, then the
 * numeric id — never a bare number presented as a name. A child is labelled
 * `Loft / wifi0` by `entityLabel`, because "wifi0" on its own does not say which
 * AP it is on — and every AP has one.
 *
 * INTEGRATE NOTE: candidate to promote into `src/components/ui`.
 */

/** Hover text: the full label, then the native id (a MAC).
 *
 * The label leads because the Issues list's Entity column is narrow and clips
 * `Primary Bedroom / wifi1` mid-word — the hover has to be able to finish the
 * sentence the cell started. `entityLabel` already carries the parent, so there
 * is nothing to append about it. */
function entityTitle(entity: EntityRef, label: string): string | undefined {
  const parts = [label, entity.native_id].filter(Boolean);
  return parts.length ? parts.join(' · ') : undefined;
}

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
      <span className={cn('t-body', className)} style={{ color }} title={entityTitle(entity, label)}>
        {label}
      </span>
    );
  }

  return (
    <Link
      to={href}
      className={cn('t-body hover:underline', className)}
      style={{ color: muted ? 'var(--fg-muted)' : 'var(--accent)' }}
      title={entityTitle(entity, label)}
      onClick={(e) => e.stopPropagation()}
    >
      {label}
    </Link>
  );
}
