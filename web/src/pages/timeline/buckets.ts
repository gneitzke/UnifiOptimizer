/**
 * Client-side bucketing for the event-density chart. The /api/events endpoint
 * returns raw events newest-first (hard-capped); we bin them into fixed-width
 * time buckets for the density bars. Empty buckets stay empty (rendered as a
 * gap, never back-filled — never-do rule 8).
 */

import type { NetEvent } from '../../api';
import { familyOf, isFaultKey, type FamilyId } from './eventKeys';

export interface WindowSpec {
  id: string;
  label: string;
  seconds: number;
  /** Target number of buckets across the window. */
  buckets: number;
}

export const WINDOWS: WindowSpec[] = [
  { id: '1h', label: '1H', seconds: 3600, buckets: 60 },
  { id: '6h', label: '6H', seconds: 6 * 3600, buckets: 72 },
  { id: '24h', label: '24H', seconds: 24 * 3600, buckets: 48 },
  { id: '7d', label: '7D', seconds: 7 * 24 * 3600, buckets: 56 },
];

export interface Bucket {
  /** Bucket start, epoch seconds. */
  t0: number;
  /** Bucket end (exclusive), epoch seconds. */
  t1: number;
  total: number;
  faults: number;
  /** Per-family counts, for the (family-filtered) totals. */
  byFamily: Record<FamilyId, number>;
}

function emptyFamilies(): Record<FamilyId, number> {
  return { wifi: 0, wired: 0, ap: 0, switch: 0, gateway: 0, wan: 0, other: 0 };
}

/**
 * Bin events into `spec.buckets` fixed-width buckets ending at `nowTs`.
 * `families` (when non-empty) restricts which events are counted; an empty set
 * means "all families".
 */
export function bucketize(
  events: NetEvent[],
  spec: WindowSpec,
  nowTs: number,
  families: Set<FamilyId>,
): Bucket[] {
  const width = Math.max(1, Math.floor(spec.seconds / spec.buckets));
  const start = nowTs - spec.buckets * width;
  const buckets: Bucket[] = [];
  for (let i = 0; i < spec.buckets; i++) {
    const t0 = start + i * width;
    buckets.push({ t0, t1: t0 + width, total: 0, faults: 0, byFamily: emptyFamilies() });
  }

  const filterOn = families.size > 0;
  for (const e of events) {
    if (e.ts < start || e.ts >= nowTs) continue;
    const fam = familyOf(e.key);
    if (filterOn && !families.has(fam)) continue;
    const idx = Math.min(buckets.length - 1, Math.floor((e.ts - start) / width));
    const b = buckets[idx];
    b.total += 1;
    b.byFamily[fam] += 1;
    if (isFaultKey(e.key)) b.faults += 1;
  }
  return buckets;
}

/** Events falling inside one bucket, respecting the family filter, newest first. */
export function eventsInBucket(
  events: NetEvent[],
  bucket: Bucket,
  families: Set<FamilyId>,
): NetEvent[] {
  const filterOn = families.size > 0;
  return events.filter(
    (e) =>
      e.ts >= bucket.t0 &&
      e.ts < bucket.t1 &&
      (!filterOn || families.has(familyOf(e.key))),
  );
}
