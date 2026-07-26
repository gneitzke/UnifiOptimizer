/**
 * Presentation-only formatting for the report (store is UTC; display local, per
 * docs/DESIGN_FOUNDATION.md §Time). These turn a backend timestamp into a label —
 * they never derive a new fact, so the "compute nothing" gate holds.
 */

/** "July 21, 2026". */
export function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

/** "Jul 21, 2026, 14:23". */
export function fmtDateTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** "Jul 14 – Jul 21, 2026" from a window's endpoints. */
export function fmtDateRange(startTs: number, endTs: number): string {
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
  const start = new Date(startTs * 1000).toLocaleDateString(undefined, opts);
  const end = new Date(endTs * 1000).toLocaleDateString(undefined, {
    ...opts,
    year: 'numeric',
  });
  return `${start} – ${end}`;
}
