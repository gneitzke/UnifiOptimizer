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
 *  sync (see `glossary` in netadmin/report/assembler.py).
 *
 *  It says "one client's minute" and means it: the `infra` SLE's minutes are a
 *  *device's* offline time and never reach a figure this sentence describes
 *  (Gitea #36, #38). Use `DOWN_MINUTE_DEFINITION` for those. */
export const FAIL_MINUTE_DEFINITION =
  "One SLE fail-minute: one minute one real client spent below a service level's pass/fail target. Counted per client, so five clients degraded for a minute is five fail-minutes. Device downtime is a separate figure in a separate unit and is never added in.";

/** The device axis's unit, and the sentence that keeps it apart from the client
 *  axis. Shown wherever a downtime figure appears without context. */
export const DOWN_MINUTE_DEFINITION =
  'One down-minute: one minute an AP, switch or gateway was itself offline, read from its own state timeline. Device time, not client time — nobody spent it as a client, so it is never added to fail-minutes.';

/** Definition for the offenders leaderboard's composite ranking number, shown
 *  as a hover tooltip on the "Burden" figure so it reads as one labelled,
 *  explained number rather than an unexplained composite next to fail-min.
 *
 *  The second sentence is the non-obvious part and is why the score is a sum of
 *  three channels rather than four: a downed AP's harm is already counted on the
 *  client axis, because its clients did not vanish — they landed on the next AP
 *  and burned coverage and roaming minutes *there*. Adding the dead AP's own
 *  downtime charges one outage twice, and since downtime accumulates easily
 *  while saying nothing about how many clients noticed, it is exactly how a loud
 *  harmless AP comes to outrank a quiet costly one. */
export const OFFENDER_BURDEN_DEFINITION =
  'Composite burden score: failed client-minutes attributed to this entity, its open issues weighted by severity, and its disconnect/roam churn. Device downtime is deliberately not in it — the clients of a downed AP move to another one and lose their minutes there, so scoring the downtime as well would charge one outage twice.';

/** SLEs whose minutes belong to a device rather than a client. Mirrors
 *  `SLE_DEVICE_AXIS_SLES` in netadmin/store/repository.py; everything else is on
 *  the client axis. A tile for one of these must label its minutes as downtime,
 *  not as fail-minutes. */
export function isDeviceAxisSle(sle: string): boolean {
  return sle === 'infra';
}

/** A measurement window as prose for a tooltip: "24 hours", "7 days". Hours up
 *  to two days, because "the last 1 day" is not how an operator says it. */
export function windowPhrase(seconds: number): string {
  const hours = Math.max(1, Math.round(seconds / 3600));
  if (hours < 48) return hours === 1 ? '1 hour' : `${hours} hours`;
  const days = Math.round(hours / 24);
  return days === 1 ? '1 day' : `${days} days`;
}

/** Hover text for the offenders leaderboard's client-minutes column.
 *
 *  Publishes the denominator and the window, which a static constant cannot:
 *  "655 minutes" means nothing until the reader knows whether 4 clients or 400
 *  were being watched, and that ambiguity is what let a mixed figure pass for a
 *  client figure in the first place. `clientsInWindow` of null means the daemon
 *  did not report the denominator, so the sentence declines to invent one. */
export function offenderClientMinutesNote(
  clientsInWindow: number | null,
  windowS: number,
): string {
  const denom =
    clientsInWindow == null
      ? 'the clients the engine judged'
      : `the ${clientCount(clientsInWindow)} the engine judged`;
  return `Minutes clients spent below a service level because of this entity, out of ${denom} in the last ${windowPhrase(windowS)}. Counted per client: five clients degraded for a minute is five client-minutes. This is what the burden score is built from.`;
}

/** Hover text for the offenders leaderboard's downtime column. Says the unit,
 *  says it is excluded from the score, and says why — in that order. */
export function offenderDownMinutesNote(windowS: number): string {
  return `How long the AP, switch or gateway was itself offline in the last ${windowPhrase(windowS)}, from its own state timeline. Device time, not client time, so it is never added to client-minutes and is not part of the burden score: the clients of a downed device move to another one and lose their minutes there. A dash means downtime was not measured, which is not the same as zero.`;
}

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

/* ---- Lifecycle legibility (Gitea #39) ------------------------------------
 *
 * "Resolving" on its own is a spinner with no end in sight, and an issue on its
 * ninety-eighth bounce used to render exactly like one clearing for the first
 * time. Both facts were already in the payload — `clear_streak` against the
 * engine's `clear_k`, and a count of the trail rows that record every killed
 * streak — so these two helpers turn them into the strings the list and the
 * detail page share. One source, so the two surfaces cannot drift.
 */

/** "3 of 6 clean checks" while an issue is resolving, else null.
 *
 * Only for the resolving state: a `clear_streak` of 0 on an active issue is not
 * progress towards anything, and a resolved one has already arrived. */
export function clearProgressLabel(issue: IssueRow): string | null {
  const k = issue.lifecycle?.clear_k;
  if (issue.state !== 'resolving' || k == null || k <= 0) return null;
  return `${issue.clear_streak} of ${k} clean checks`;
}

/** The full sentence behind that fraction — hover and screen-reader text. */
export function clearProgressNote(issue: IssueRow): string | null {
  const k = issue.lifecycle?.clear_k;
  if (issue.state !== 'resolving' || k == null || k <= 0) return null;
  const done = issue.clear_streak;
  return `Clean on ${done} ${done === 1 ? 'check' : 'checks'} in a row since it last fired. It resolves at ${k} in a row; one more occurrence puts the count back to zero.`;
}

/** "12×" for a recurring issue, else null — the compact badge in the list. */
export function recurrenceBadgeLabel(issue: IssueRow): string | null {
  const lc = issue.lifecycle;
  if (!lc?.recurring) return null;
  return `${lc.streak_resets_7d}×`;
}

/** "came back 12 times this week" — the phrase the detail header appends. */
export function recurrencePhrase(issue: IssueRow): string | null {
  const lc = issue.lifecycle;
  if (!lc?.recurring) return null;
  const n = lc.streak_resets_7d;
  return `came back ${n} ${n === 1 ? 'time' : 'times'} this week`;
}

/** Hover and screen-reader text for the recurrence badge: what was counted, and
 *  why it is not the occurrence count sitting next to it. */
export function recurrenceNote(issue: IssueRow): string | null {
  const lc = issue.lifecycle;
  if (!lc?.recurring) return null;
  const n = lc.streak_resets_7d;
  return `Recurring: it came back ${n} ${n === 1 ? 'time' : 'times'} in the last 7 days, each time resetting the count of clean checks. That is how often it returned, not how often it fired.`;
}

/* ---- Operator suppression (Gitea #49) ------------------------------------
 *
 * Suppression is the one attention mute: an operator parks an issue's claim on
 * counts, badges and alerts without touching a measured number. It is DERIVED
 * here at read time from three row fields plus `now` — the single source on the
 * web side, mirroring `netadmin/issues/suppression.py`, so the sidebar badge, the
 * issues list, the dashboard and the detail page all agree. Two clocks (server
 * `time.time()` vs the browser) mean a timed suppression expires at slightly
 * different instants across surfaces; that is the same harmless skew the snooze
 * caption already had, and it self-heals on the next poll.
 *
 * The disclosure rule is absolute: any count that shrank because of suppression
 * must name the amount it shrank by ("9 open · 3 suppressed"), never a silent 6.
 */

/** The minimal shape suppression is derived from — a structural subset that both
 * `IssueRow` (pages) and `Issue` (shell/api) satisfy, so this stays the single
 * source of the rule on the web side. */
export interface Suppressible {
  severity: Severity;
  suppressed_ts?: number | null;
  suppress_until_ts?: number | null;
  suppressed_severity?: Severity | null;
}

/** Is this issue suppressed *right now* (the three rules): a set `suppressed_ts`,
 * not past `suppress_until_ts`, and current severity not more severe than the
 * severity captured at suppression (an escalation past it voids the mute). */
export function isSuppressedNow(issue: Suppressible, nowSec: number): boolean {
  const ts = issue.suppressed_ts;
  if (ts == null) return false;
  const until = issue.suppress_until_ts;
  if (until != null && nowSec >= until) return false;
  const captured = issue.suppressed_severity;
  if (captured != null && severityRank(issue.severity) < severityRank(captured)) return false;
  return true;
}

/** "9 open · 3 suppressed" — the disclosed count. When nothing is suppressed it
 * is just "9 open", so a surface can use it unconditionally. `noun` lets a
 * caller say "issues" where the context needs it. */
export function disclosedOpenCount(open: number, suppressed: number, noun = ''): string {
  const unit = noun ? ` ${noun}` : '';
  const head = `${open} open${unit}`;
  return suppressed > 0 ? `${head} · ${suppressed} suppressed` : head;
}

/** Hover / screen-reader sentence for a suppressed issue's badge and detail line:
 * when it was muted, until when, and — load-bearing — that measured impact is
 * untouched, so nobody reads the mute as "the harm went away". */
export function suppressionNote(issue: IssueRow, nowSec: number): string {
  const since = issue.suppressed_ts != null ? formatDuration(nowSec - issue.suppressed_ts) : null;
  const sinceClause = since ? `Suppressed ${since} ago` : 'Suppressed';
  const until = issue.suppress_until_ts;
  const untilClause = until != null ? `, until it expires` : ', until unsuppressed';
  return `${sinceClause}${untilClause}. Excluded from counts and alerts; measured impact is unchanged.`;
}

/** The two severities when an issue's suppression was lifted *specifically*
 * because its severity escalated past the one captured at suppression — the
 * captured severity and the current one, so a caller can say "rose from Low to
 * Critical". Null in every other case: never suppressed, still suppressed, or
 * lifted only by expiry.
 *
 * Escalation-void is DERIVED (rule 3 of `isSuppressedNow`): no `unsuppressed`
 * event fires at the instant it lifts, so without this note an operator sees a
 * muted issue silently return to the counts with nothing explaining why. Expiry
 * is deliberately excluded — the `suppressed` trail row already dates the "until"
 * — so this note names the one lift the trail otherwise cannot. */
export function suppressionEscalationVoid(
  issue: Suppressible,
  nowSec: number,
): { from: Severity; to: Severity } | null {
  const captured = issue.suppressed_severity;
  if (issue.suppressed_ts == null || captured == null) return null;
  if (isSuppressedNow(issue, nowSec)) return null;
  // Not expiry: an expired suppression is dated by its own `suppressed` trail row,
  // so the escalation note stays silent and the two lifts never double up.
  const until = issue.suppress_until_ts;
  if (until != null && nowSec >= until) return null;
  if (severityRank(issue.severity) < severityRank(captured)) {
    return { from: captured, to: issue.severity };
  }
  return null;
}

/* ---- Issue impact (the Issues list's Impact column, Gitea #24, #36) ------ */

/** What the Impact column measures, as a sentence — the column header's hover
 *  text. Two quantities, and the header's job is to say they are two: client
 *  minutes and device down-minutes are different units over different
 *  populations, and the figure that summed them credited a switch outage with
 *  minutes no client experienced (Gitea #36). Each figure's own tooltip carries
 *  the denominator and the window, which come from the payload and so cannot be
 *  stated here without going stale. */
export const ISSUE_IMPACT_DEFINITION =
  'Two separate quantities, never added. Clients: how many clients lost SLE minutes while this issue was open, and how many minutes they lost, out of the clients the engine judged in the window. Down: how long the AP, switch or gateway itself was offline — device time, which nobody spent as a client. A dash means nothing was measured, which is not the same as zero.';

/** The impact window as prose. Hours, not `formatDurationLong`'s "1 day": the
 *  sentences read "within the last 24 hours", which is how an operator thinks
 *  about a measurement window, and "the last 1 day" is not English. */
function impactWindowLabel(seconds: number): string {
  const hours = Math.round(seconds / 3600);
  return hours === 1 ? '1 hour' : `${hours} hours`;
}

/** Minutes as display text. Precision follows what the number can carry: a
 *  large total rounds to whole minutes (a tenth of a minute is noise against
 *  it), a small one keeps its decimal so a real-but-small cost does not round
 *  away to a zero it would then be mistaken for. */
export function formatImpactMinutes(minutes: number): string {
  if (minutes <= 0) return '0';
  if (minutes < 0.05) return '<0.1';
  if (minutes < 10) return String(Math.round(minutes * 10) / 10);
  return Math.round(minutes).toLocaleString();
}

/** "clients", never "devices" (an AP or switch, to a UniFi admin) and never
 *  "users" (an administrator, in UniFi's Alarm Manager). */
function clientCount(n: number): string {
  return n === 1 ? '1 client' : `${n} clients`;
}

/** Entity types the infra SLE walks a state timeline for — the ones that have a
 *  downtime axis at all. Mirrors `SLE_DEVICE_AXIS_ENTITY_TYPES` in
 *  `netadmin/store/repository.py`; a radio is absent from both. */
const INFRA_ENTITY_TYPES: readonly (string | null)[] = ['ap', 'switch', 'gateway'];

/** The piece of infrastructure, in the reader's own vocabulary. `ap` is an
 *  initialism and stays capitalised; the rest are ordinary nouns. Naming the
 *  specific kind beats the generic "device", which a UniFi admin reads as
 *  "some AP or switch" and which is never allowed to stand in for "client". */
function deviceWord(entityType: string | null | undefined): string {
  if (entityType === 'ap') return 'AP';
  if (entityType === 'switch') return 'switch';
  if (entityType === 'gateway') return 'gateway';
  if (entityType === 'radio') return 'radio';
  return 'device';
}

export interface ImpactLine {
  /** The figure as text, e.g. "3 clients" or "AP down 42 min". */
  text: string;
  /** True only for a real, measured zero, so it can read quieter than a cost. */
  zero: boolean;
}

export interface ImpactDisplay {
  /** The headline figure, or null when neither axis was measured — in which
   *  case the caller renders a dash. Never a stand-in zero. */
  primary: ImpactLine | null;
  /** The qualifying second line, or null when there is nothing more to add.
   *  Always the *other* axis or the primary's own multiplier — never a total,
   *  because the two axes have no meaningful sum. */
  secondary: ImpactLine | null;
  /** Everything the figures say, with denominators and window. Hover text on
   *  every surface, and the screen-reader text behind the dash. */
  note: string;
}

/** An issue's impact as the list column and the detail page both show it.
 *
 *  One function so the two surfaces cannot drift, and so every "no figure"
 *  branch is forced to say *why*. Two rules it exists to enforce:
 *
 *  1. **The axes never merge.** A client-experience finding leads with the
 *     client count and carries the minutes as its multiplier; an infrastructure
 *     finding leads with the device's own downtime. Whichever leads, the other
 *     appears beside it as a separate figure, never summed into it.
 *  2. **"Not measured" is not "zero".** Nothing measured renders a dash with
 *     its reason; a real measured zero renders as a zero, quietly. Presenting
 *     the first as the second would let an unwatched outage read as harmless. */
export function impactDisplay(issue: IssueRow, nowSec: number): ImpactDisplay {
  const impact: IssueImpact | null | undefined = issue.impact;
  const absent = (note: string): ImpactDisplay => ({ primary: null, secondary: null, note });

  // `client`/`infra` are guarded as well as `impact` itself: a UI running ahead
  // of its daemon degrades to "not measured" rather than throwing on the older
  // single-figure payload.
  if (!impact || !impact.client || !impact.infra) {
    return absent('This daemon did not report an impact figure for this issue.');
  }
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
  if (!impact.measured) {
    if (issue.resolved_ts != null && issue.resolved_ts <= nowSec - impact.window_s) {
      return absent(
        `This issue closed before the last ${impactWindowLabel(impact.window_s)} began, so none of its life falls in the measured window.`,
      );
    }
    return absent('No SLE measurements cover the hours this issue was open.');
  }

  const window = impactWindowLabel(impact.window_s);
  const c = impact.client;
  const i = impact.infra;

  // ---- client axis -------------------------------------------------------
  let clientLine: ImpactLine | null = null;
  let clientNote: string | null = null;
  if (c.measured && c.clients != null && c.fail_minutes != null) {
    const denom = c.clients_in_window ?? 0;
    if (c.fail_minutes > 0) {
      clientLine = {
        text: `${clientCount(c.clients)} · ${formatImpactMinutes(c.fail_minutes)} min`,
        zero: false,
      };
      clientNote =
        impact.basis === 'own'
          ? `This client lost ${formatImpactMinutes(c.fail_minutes)} SLE minutes while this issue was open — one of the ${clientCount(denom)} the engine judged in the last ${window}.`
          : `${c.clients} of the ${clientCount(denom)} the engine judged in the last ${window} lost SLE minutes pinned on this ${deviceWord(issue.entity.type)} while this issue was open: ${formatImpactMinutes(c.fail_minutes)} minutes in total.`;
    } else {
      clientLine = { text: clientCount(0), zero: true };
      clientNote = `Measured: none of the ${clientCount(denom)} the engine judged in the last ${window} lost a minute to this issue.`;
    }
  } else if (i.measured) {
    clientNote = 'No client-minute measurements cover the hours this issue was open.';
  }

  // ---- device axis -------------------------------------------------------
  let infraLine: ImpactLine | null = null;
  let infraNote: string | null = null;
  if (i.measured && i.down_minutes != null) {
    const word = deviceWord(i.entity_type);
    if (i.down_minutes > 0) {
      infraLine = { text: `${word} down ${formatImpactMinutes(i.down_minutes)} min`, zero: false };
      infraNote = `The ${word} itself was down ${formatImpactMinutes(i.down_minutes)} minutes while this issue was open, from its own state timeline. Device time is not client time, so the two figures are never added.`;
    } else {
      infraNote = `Measured: the ${word} itself never went down while this issue was open.`;
    }
  } else if (INFRA_ENTITY_TYPES.includes(issue.entity.type)) {
    // The entity has a downtime axis but nothing judged it here. Saying so
    // beats silence, which a reader would take as "it stayed up".
    infraNote = `Downtime for this ${deviceWord(issue.entity.type)} was not measured over these hours.`;
  }

  // Downtime leads when there is any: an offline AP or switch is the fact the
  // row is about, and its client cost (often none) reads as the qualifier. The
  // sentences follow the same order, so the tooltip explains the top line first.
  const primary = infraLine ?? clientLine;
  const secondary = infraLine ? clientLine : null;
  const ordered = infraLine ? [infraNote, clientNote] : [clientNote, infraNote];
  return { primary, secondary, note: ordered.filter(Boolean).join(' ') };
}

/** Sort key: most severe first (p1 < p2 < p3). */
export function severityRank(s: Severity): number {
  return s === 'p1' ? 0 : s === 'p2' ? 1 : 2;
}

/** The one severity word the app and the report both speak (Gitea #22): P1 =
 * Critical, P2 = High, P3 = Low. Mirrors `SeverityPill`'s own labels so a
 * sentence naming a severity ("rose from Low to Critical") reads the same word
 * the pill next to it shows. */
const SEVERITY_LABELS: Record<Severity, string> = { p1: 'Critical', p2: 'High', p3: 'Low' };
export function severityLabel(s: Severity): string {
  return SEVERITY_LABELS[s] ?? s;
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
