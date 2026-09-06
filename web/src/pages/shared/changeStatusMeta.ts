/**
 * Pure status vocabulary for change/fix-attempt rows (audit U1/U4/U5) — split
 * out of `changeStatus.tsx` (which stays component-only) so both stay
 * fast-refresh friendly.
 */

export type ChangeStatus =
  | 'applying'
  | 'applied'
  | 'failed'
  | 'unknown'
  | 'reverted'
  | 'revert_unknown';

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
  // Same caution tone as `unknown` (an ambiguous APPLY) — this is deliberately
  // the mirror case: the REVERT of this change was sent but never confirmed,
  // so the change may or may not still be in place. Reusing the tone keeps
  // "ambiguous outcome" reading as one visual language; the label is the only
  // thing that has to carry which direction (apply vs. revert) is uncertain.
  revert_unknown: {
    label: 'Revert unknown',
    color: 'var(--sev-p3)',
    fill: 'var(--sev-p3-fill)',
  },
};

/** Normalize a raw ledger status string to one of the known states. An older
 * daemon (pre-dating `applying`/`unknown`/`revert_unknown`) or any value this
 * build doesn't recognize degrades to `unknown` rather than rendering nothing
 * or silently mislabeling it — the whole point of this audit item is that an
 * ambiguous state must never look like a settled one. `revert_unknown` is
 * kept distinct from `unknown`: the former means the REVERT's outcome is
 * uncertain (and the backend permanently refuses to retry it), the latter
 * means the original APPLY's outcome is uncertain. */
export function normalizeChangeStatus(status: string): ChangeStatus {
  switch (status) {
    case 'applying':
    case 'applied':
    case 'failed':
    case 'unknown':
    case 'reverted':
    case 'revert_unknown':
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
    case 'revert_unknown':
      return 2;
    case 'applying':
      return 1;
    case 'applied':
    case 'reverted':
      return 0;
  }
}
