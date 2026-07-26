/**
 * Formatting helpers shared by the dashboard + issues surfaces.
 *
 * INTEGRATE NOTE: duration + label helpers are good candidates to promote into
 * `src/components/ui` (alongside RelativeTime) at the integrate pass.
 */

import type { IssueState, Severity } from '../../api/types';
import type { IssueRow } from './api';

/** Compact human duration: "6d", "5h 12m", "3m", "45s", "just now". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 5) return 'just now';
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) {
    const rem = m % 60;
    return rem ? `${h}h ${rem}m` : `${h}h`;
  }
  const d = Math.floor(h / 24);
  const remH = h % 24;
  return remH ? `${d}d ${remH}h` : `${d}d`;
}

/** Long human duration for prose: "6 days", "5 hours", "3 minutes". */
export function formatDurationLong(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return s === 1 ? '1 second' : `${s} seconds`;
  const m = Math.floor(s / 60);
  if (m < 60) return m === 1 ? '1 minute' : `${m} minutes`;
  const h = Math.floor(m / 60);
  if (h < 24) return h === 1 ? '1 hour' : `${h} hours`;
  const d = Math.floor(h / 24);
  return d === 1 ? '1 day' : `${d} days`;
}

/** Seconds an issue has existed: to now while open, to resolved_ts once closed. */
export function issueDurationSeconds(issue: IssueRow, nowSec: number): number {
  const end =
    issue.state === 'resolved' && issue.resolved_ts != null
      ? issue.resolved_ts
      : nowSec;
  return end - issue.first_seen_ts;
}

/** "ongoing 6d" while open, "lasted 6d" once resolved — the duration column. */
export function ongoingLabel(issue: IssueRow, nowSec: number): string {
  const dur = formatDuration(issueDurationSeconds(issue, nowSec));
  return issue.state === 'resolved' ? `lasted ${dur}` : `ongoing ${dur}`;
}

/** Sort key: most severe first (p1 < p2 < p3). */
export function severityRank(s: Severity): number {
  return s === 'p1' ? 0 : s === 'p2' ? 1 : 2;
}

/** Sort key grouping open work above resolved: active < resolving < pending < resolved. */
export function stateRank(s: IssueState): number {
  return s === 'active' ? 0 : s === 'resolving' ? 1 : s === 'pending' ? 2 : 3;
}

const SLE_LABELS: Record<string, string> = {
  coverage: 'Coverage',
  capacity: 'Capacity',
  connect: 'Connectivity',
  roaming: 'Roaming',
  wan: 'WAN',
  infra: 'Infrastructure',
};

export function sleLabel(key: string): string {
  return SLE_LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

const ACRONYMS: Record<string, string> = {
  wan: 'WAN',
  dns: 'DNS',
  dhcp: 'DHCP',
  isp: 'ISP',
  rssi: 'RSSI',
  ap: 'AP',
  poe: 'PoE',
  sfp: 'SFP',
  stp: 'STP',
  wifi: 'Wi-Fi',
  cci: 'CCI',
  ci: 'CCI',
};

/** snake_case classifier / evidence key -> readable words, keeping acronyms. */
export function humanizeKey(key: string): string {
  return key
    .split(/[_.]/)
    .filter(Boolean)
    .map((w) => ACRONYMS[w] ?? w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** 0..1 fractional score -> 0..100 integer, or null when there was no data. */
export function scoreTo100(score: number | null | undefined): number | null {
  if (score == null || !Number.isFinite(score)) return null;
  return Math.round(score * 100);
}

/** Band a 0-100 SLE score falls in (color is applied at the call site). */
export function scoreBand(score100: number | null): 'good' | 'fair' | 'poor' | 'none' {
  if (score100 == null) return 'none';
  if (score100 >= 90) return 'good';
  if (score100 >= 75) return 'fair';
  return 'poor';
}
