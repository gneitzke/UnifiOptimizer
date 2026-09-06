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
  | 'reverting'
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
  // `reverting` is written durably to the ledger BEFORE the revert PUT is
  // sent, so a crash/cancel between that write and the PUT leaves the row
  // stuck here forever — the backend permanently refuses to replay a revert
  // for it. Normally this is a fleeting in-flight state, but the UI can't
  // assume it resolves, so it gets the same caution tone as `unknown` /
  // `revert_unknown` (never the neutral "applying" accent, which would read
  // as "still working, wait for it") with its own label distinguishing an
  // interrupted REVERT from an ambiguous APPLY (`unknown`) or a REVERT whose
  // send merely went unconfirmed (`revert_unknown`).
  reverting: {
    label: 'Revert interrupted',
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
 * means the original APPLY's outcome is uncertain. `reverting` is a third,
 * separate state: the revert was durably recorded as started but never
 * finished (crash/cancel mid-send) — the backend refuses to retry it, just
 * like `revert_unknown`, but the row never even got a confirmed send. */
export function normalizeChangeStatus(status: string): ChangeStatus {
  switch (status) {
    case 'applying':
    case 'applied':
    case 'failed':
    case 'unknown':
    case 'reverted':
    case 'reverting':
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
    case 'reverting':
      return 2;
    case 'applying':
      return 1;
    case 'applied':
    case 'reverted':
      return 0;
  }
}
