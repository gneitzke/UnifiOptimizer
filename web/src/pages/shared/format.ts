/**
 * Formatting helpers shared by the dashboard + issues surfaces.
 *
 * INTEGRATE NOTE: duration + label helpers are good candidates to promote into
 * `src/components/ui` (alongside RelativeTime) at the integrate pass.
 */

import type { IssueState, Severity } from '../../api/types';
import type { IssueImpact, IssueRow } from './api';

/** Definition surfaced (as a hover tooltip) everywhere the raw "fail-min" unit
 *  appears without context: the dashboard offenders list, the SLE "Why"
 *  breakdown. The report's Appendix glossary carries the print-safe
 *  equivalent, since a tooltip does not survive PDF export. Keep the two in
 *  sync (see `glossary` in netadmin/report/assembler.py). */
export const FAIL_MINUTE_DEFINITION =
  "One SLE fail-minute: a real client's minute that missed a service level's pass/fail target.";

/** Definition for the offenders leaderboard's composite ranking number, shown
 *  as a hover tooltip on the "Burden" figure so it reads as one labelled,
 *  explained number rather than an unexplained composite next to fail-min. */
export const OFFENDER_BURDEN_DEFINITION =
  'Composite burden score: a weighted blend of failed SLE minutes, open issues, and disconnect/roam events, used to rank offenders.';

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

/* ---- Issue impact (the Issues list's Impact column, Gitea #24) ----------- */

/** What the Impact column measures, as a sentence — the column header's hover
 *  text. The window is left to each figure's own tooltip (it comes from the
 *  payload, so the header cannot state it without going stale); what the header
 *  owes the reader is the unit and the dash rule. */
export const ISSUE_IMPACT_DEFINITION =
  'Failed SLE client-minutes this issue accounts for — real minutes real clients spent degraded while it was open. A dash means nothing was measured, which is not the same as zero.';

/** The impact window as prose. Hours, not `formatDurationLong`'s "1 day": the
 *  sentences read "within the last 24 hours", which is how an operator thinks
 *  about a measurement window, and "the last 1 day" is not English. */
function impactWindowLabel(seconds: number): string {
  const hours = Math.round(seconds / 3600);
  return hours === 1 ? '1 hour' : `${hours} hours`;
}

/** Fail-minutes as display text. Precision follows what the number can carry: a
 *  large total rounds to whole minutes (a tenth of a minute is noise against
 *  it), a small one keeps its decimal so a real-but-small cost does not round
 *  away to a zero it would then be mistaken for. */
export function formatFailMinutes(minutes: number): string {
  if (minutes <= 0) return '0';
  if (minutes < 0.05) return '<0.1';
  if (minutes < 10) return String(Math.round(minutes * 10) / 10);
  return Math.round(minutes).toLocaleString();
}

export interface ImpactDisplay {
  /** The figure as text, or null when nothing was measured — in which case the
   *  caller renders a dash. Never a stand-in zero. */
  text: string | null;
  /** True only for a real, measured zero, so it can read quieter than a cost. */
  zero: boolean;
  /** The sentence that qualifies the figure, or explains its absence. */
  note: string;
}

/** An issue's impact as the list column and the detail page both show it.
 *
 *  One function so the two surfaces cannot drift, and so every "no figure"
 *  branch is forced to say *why*. "Nothing was measured" and "it was measured
 *  and cost nobody anything" are different facts; presenting the first as the
 *  second would let an unwatched outage read as harmless. */
export function impactDisplay(issue: IssueRow, nowSec: number): ImpactDisplay {
  const impact: IssueImpact | null | undefined = issue.impact;
  const absent = (note: string): ImpactDisplay => ({ text: null, zero: false, note });

  if (!impact) return absent('This daemon did not report an impact figure for this issue.');
  if (!issue.entity) {
    return absent('Network-wide: there is no entity to attribute failed client-minutes to.');
  }
  if (impact.basis === null) {
    const what =
      issue.entity.type === 'port'
        ? 'a port'
        : issue.entity.type === 'wlan'
          ? 'a WLAN'
          : 'this kind of entity';
    return absent(
      `Failed client-minutes are never recorded against ${what}, so this issue has no figure of its own.`,
    );
  }
  if (!impact.measured || impact.fail_minutes == null) {
    if (issue.resolved_ts != null && issue.resolved_ts <= nowSec - impact.window_s) {
      return absent(
        `This issue closed before the last ${impactWindowLabel(impact.window_s)} began, so none of its life falls in the measured window.`,
      );
    }
    return absent('No SLE measurements cover the hours this issue was open.');
  }

  const zero = impact.fail_minutes === 0;
  return {
    text: formatFailMinutes(impact.fail_minutes),
    zero,
    note: zero
      ? 'Measured over the window: no failed client-minutes are attributed to this issue.'
      : impact.basis === 'own'
        ? `This client's own failed SLE minutes over the hours this issue was open, within the last ${impactWindowLabel(impact.window_s)}.`
        : `Failed SLE client-minutes attributed to this entity over the hours this issue was open, within the last ${impactWindowLabel(impact.window_s)}.`,
  };
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

/* ---- Evidence value formatting (issue detail's EvidenceView, Gitea #18) --- */

/** A number as a short decimal string: an integer stays bare, else 3 sig-figs. */
export function numberStr(n: number): string {
  if (Number.isInteger(n)) return String(n);
  const r = Math.round(n * 1000) / 1000;
  return String(r);
}

/** A scalar evidence value as display text: '—' for null, Yes/No for bool. */
export function scalarText(v: unknown): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return Number.isFinite(v) ? numberStr(v) : '—';
  return String(v);
}

export interface InferredUnit {
  unit: string;
  /** True when the raw value is a 0..1 fraction that displays ×100. */
  percent: boolean;
  /** True when the raw value is seconds that should display compact. */
  duration: boolean;
}

// Conservative snake_case-suffix -> unit inference: only the LAST underscore
// segment is checked, so a key merely containing one of these substrings never
// misfires ("occurrences" has no "_s" segment; "window_short_s" does). This is
// the fallback for evidence keys a detector's catalog Playbook hasn't labeled
// with an explicit EvidenceField yet (see IssueDetailResponse.evidence_layout)
// — it never overrides an explicit label/unit from the API.
const UNIT_SUFFIXES: Record<string, string> = {
  ms: 'ms',
  dbm: 'dBm',
  mhz: 'MHz',
  ghz: 'GHz',
  mbps: 'Mbps',
  kbps: 'kbps',
  w: 'W',
  db: 'dB',
};

/** Infer a display unit from an evidence key's last snake_case segment, or
 * null when nothing matches. `_fraction` -> percent (value ×100); `_pct` ->
 * percent (value already 0-100); `_s` -> a compact duration, not a bare "N s"
 * (a detector's analysis window reads better as "10 min" than "600 s"). */
export function inferUnit(key: string): InferredUnit | null {
  const segments = key.split(/[_.]/).filter(Boolean);
  const last = segments[segments.length - 1]?.toLowerCase();
  if (!last) return null;
  if (last === 'fraction') return { unit: '%', percent: true, duration: false };
  if (last === 'pct' || last === 'percent') return { unit: '%', percent: false, duration: false };
  if (last === 's') return { unit: '', percent: false, duration: true };
  const unit = UNIT_SUFFIXES[last];
  return unit ? { unit, percent: false, duration: false } : null;
}

/** A key's label with its unit-bearing suffix segment dropped before
 * humanizing, so an inferred unit is never doubled ("RSSI Dbm" next to its
 * own "(dBm)"/" dBm"). `rssi_dbm` + {unit:"dBm"} -> "RSSI", not "RSSI Dbm". A
 * key with no inferred unit humanizes in full, unchanged. */
export function humanizeKeyForUnit(key: string, inferred: InferredUnit | null): string {
  if (!inferred) return humanizeKey(key);
  const segments = key.split(/[_.]/).filter(Boolean);
  const withoutSuffix = segments.slice(0, -1).join('_');
  return humanizeKey(withoutSuffix || key);
}

/** Seconds as a compact duration ("45 s", "10 min", "1 h") for an evidence
 * value. Deliberately NOT `formatDuration` above: that one reads "just now"
 * below 5 seconds, which is right for "how long ago" but wrong for a plain
 * duration value like a 3-second burst-gap threshold. */
export function compactSeconds(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${Math.round(s / 3600)} h`;
}

/** A scalar value formatted with a unit ("52 ms", "0.4%", "2.89×", "10 min");
 * `percent` multiplies by 100 first, `duration` renders via `compactSeconds`
 * (and wins over `unit`/`percent`). Accepts a numeric-looking string too — a
 * detector's evidence dict carries band/channel numbers as strings (dict
 * values need not all share a type), and "2.4" with a GHz unit is still a
 * unit-bearing number, not free text. Anything else falls back to `scalarText`. */
export function formatWithUnit(
  value: unknown,
  unit: string,
  percent: boolean,
  duration = false,
): string {
  const n =
    typeof value === 'number'
      ? value
      : typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))
        ? Number(value)
        : null;
  if (n == null) return scalarText(value);
  if (duration) return compactSeconds(n);
  const shown = percent ? n * 100 : n;
  const text = numberStr(shown);
  if (!unit) return text;
  return unit === '%' || unit === '×' ? `${text}${unit}` : `${text} ${unit}`;
}

/** Band a 0-100 SLE score falls in (color is applied at the call site). */
export function scoreBand(score100: number | null): 'good' | 'fair' | 'poor' | 'none' {
  if (score100 == null) return 'none';
  if (score100 >= 90) return 'good';
  if (score100 >= 75) return 'fair';
  return 'poor';
}
