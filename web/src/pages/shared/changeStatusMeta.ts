/**
 * Pure status vocabulary for change/fix-attempt rows (audit U1/U4/U5) — split
 * out of `changeStatus.tsx` (which stays component-only) so both stay
 * fast-refresh friendly.
 */

export type ChangeStatus = 'applying' | 'applied' | 'failed' | 'unknown' | 'reverted';

export interface ChangeStatusMeta {
  label: string;
  color: string;
  fill: string;
}

export const CHANGE_STATUS_META: Record<ChangeStatus, ChangeStatusMeta> = {
  applying: {
    label: 'Applying…',
    color: 'var(--accent)',
    fill: 'color-mix(in srgb, var(--accent) 12%, transparent)',
  },
  applied: {
    label: 'Applied',
    color: 'var(--sev-healthy)',
    fill: 'var(--sev-healthy-fill)',
  },
  failed: {
    label: 'Failed',
    color: 'var(--sev-p1)',
    fill: 'var(--sev-p1-fill)',
  },
  unknown: {
    label: 'Unknown',
    color: 'var(--sev-p3)',
    fill: 'var(--sev-p3-fill)',
  },
  reverted: {
    label: 'Reverted',
    color: 'var(--sev-neutral)',
    fill: 'var(--sev-neutral-fill)',
  },
};

/** Normalize a raw ledger status string to one of the five known states. An
 * older daemon (pre-dating `applying`/`unknown`) or any value this build
 * doesn't recognize degrades to `unknown` rather than rendering nothing or
 * silently mislabeling it — the whole point of this audit item is that an
 * ambiguous state must never look like a settled one. */
export function normalizeChangeStatus(status: string): ChangeStatus {
  switch (status) {
    case 'applying':
    case 'applied':
    case 'failed':
    case 'unknown':
    case 'reverted':
      return status;
    default:
      return 'unknown';
  }
}

/** Ranks a status by how urgently it needs a human's attention — used to
 * surface failed/uncertain attempts first in the change ledger (audit U4) and
 * to pick the "worst" status when summarizing a group of steps. */
export function changeStatusUrgency(status: string): number {
  switch (normalizeChangeStatus(status)) {
    case 'failed':
    case 'unknown':
      return 2;
    case 'applying':
      return 1;
    case 'applied':
    case 'reverted':
      return 0;
  }
}
