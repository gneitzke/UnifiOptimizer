/**
 * Page-local API reconciliation layer (Phase 3, dashboard + issues surfaces).
 *
 * The shared `src/api/client.ts` was written to the contract sketch, but the
 * backend routers (the source of truth — `netadmin/server/routers/*`) return a
 * few shapes the sketch did not settle: issues and events carry a resolved
 * `entity` OBJECT (not `entity_name`); SLE offenders carry `attributed_entity_id`
 * + an `entity` ref; the metrics window returns `buckets` (not `points`) with a
 * min/max/avg/n envelope and a `tier`; ack/snooze return `{ issue }`. This module
 * fetches those endpoints with the real shapes so the pages stay honest.
 *
 * INTEGRATE NOTE: fold these types + fetchers back into `src/api` and delete this
 * file once the shared client matches the routers. It deliberately owns only the
 * endpoints these two surfaces touch, and reuses the shell's stable union
 * primitives from `src/api/types`.
 */

import { authHeaders, clearToken, getToken, promptForToken } from '../../api/token';
import type {
  EntityType,
  FixState,
  IssueLifecycle,
  IssueState,
  Severity,
} from '../../api/types';

/* ---- Shared reference shapes (match serialize.py::entity_ref) ----------- */

export interface EntityRef {
  entity_id: number;
  name: string | null;
  type: EntityType | string | null;
  native_id: string | null;
  model: string | null;
  /** The device a structural child belongs to — a port's switch, a radio's AP.
   * Null for a top-level entity. */
  parent_id: number | null;
  parent_name: string | null;
}

/** Which detail route an entity ref links to, or null when it has no own page.
 *
 * A radio and a port have no page of their own, but they are not orphans: the
 * device detail page is where their card / table row actually lives, so they
 * link there rather than sitting as dead plain text next to linked APs and
 * clients. A WLAN (and the site-wide RF pseudo-entity) has neither a page nor a
 * parent device, so it stays text — the one honest exception. */
export function entityHref(ref: EntityRef | null | undefined): string | null {
  if (!ref) return null;
  const t = ref.type;
  if (t === 'ap' || t === 'switch' || t === 'gateway') return `/devices/${ref.entity_id}`;
  if (t === 'client') return `/clients/${ref.entity_id}`;
  if ((t === 'radio' || t === 'port') && ref.parent_id != null) {
    return `/devices/${ref.parent_id}`;
  }
  return null;
}

/** Entity types whose own name only means something next to their parent. */
const CHILD_ENTITY_TYPES = new Set(['radio', 'port']);

/** How an entity is named to a human: `Loft / wifi0` for a radio or a port.
 *
 * Every AP names its radios `wifi0`/`wifi1` and every switch has a `Port 2`, so
 * four genuinely distinct saturated radios all render as `wifi0` and read as
 * duplicate rows. Prefixing the parent is the disambiguation, and it stays short
 * enough for the width-constrained Entity column — which is why it is a prefix
 * and not a parenthetical.
 *
 * A child whose parent the server could not resolve degrades to the bare name,
 * never to `null / wifi0` — and one an admin already named after its device
 * ("Loft 5G" under "Loft") is left alone, because that name already identifies.
 * Mirrors `entity_display_label` in `netadmin/domain/entities.py`; the two must
 * agree. */
export function entityLabel(ref: EntityRef | null | undefined): string {
  if (!ref) return 'unknown';
  const name = ref.name || ref.native_id || `#${ref.entity_id}`;
  const parent = ref.parent_name;
  if (!ref.type || !CHILD_ENTITY_TYPES.has(ref.type) || !parent) return name;
  if (name.toLowerCase().includes(parent.toLowerCase())) return name;
  return `${parent} / ${name}`;
}

/* ---- Issues ------------------------------------------------------------- */

export type IncidentRole = 'root' | 'symptom';

/** Which side of the SLE ledger an issue's entity sits on. `attributed` — the
 * failed client-minutes the engine pins on this AP/switch/gateway/radio;
 * `own` — a client's own failed minutes; `null` — the engine records nothing
 * against this kind of entity (a port, a WLAN, a network-wide issue), so there
 * is no figure to quote. */
export type ImpactBasis = 'attributed' | 'own';

/** The client-experience axis: what real clients lived through.
 *
 * `measured: false` always comes with null figures, never `0` — a zero here
 * means "the window was judged and nothing failed", and an unmeasured outage
 * rendered as a zero would read as harmless. */
export interface IssueImpactClientAxis {
  measured: boolean;
  /** Distinct clients that had failed SLE minutes while the issue was open. */
  clients: number | null;
  /** Those clients' failed minutes, summed. Never added to `infra.down_minutes`. */
  fail_minutes: number | null;
  /** The denominator: clients the SLE engine judged anywhere in the window. */
  clients_in_window: number | null;
}

/** The device axis: how long the AP, switch or gateway itself was down.
 *
 * Device-minutes, integrated from the device's own state timeline. Nobody
 * experienced them as a client, which is why they live in their own block. */
export interface IssueImpactInfraAxis {
  measured: boolean;
  down_minutes: number | null;
  /** `ap` / `switch` / `gateway` — what was down, for the label. */
  entity_type: string | null;
}

/** What an issue has cost, or an explicit statement that nobody knows.
 *
 * **Two quantities, never added** (Gitea #36). Client-minutes and device
 * down-minutes are different units over different populations; there is
 * deliberately no combined field, because summing them credited a switch
 * outage with minutes no client experienced. Source of truth:
 * `netadmin/server/routers/issues.py::_impact`. */
export interface IssueImpact {
  /** How far back the figures look, in seconds (the API uses 24 h). */
  window_s: number;
  basis: ImpactBasis | null;
  /** True when at least one axis carries a figure. */
  measured: boolean;
  client: IssueImpactClientAxis;
  infra: IssueImpactInfraAxis;
}

export interface IssueRow {
  id: number;
  fingerprint: string;
  detector_key: string;
  entity_id: number | null;
  entity: EntityRef | null;
  severity: Severity;
  state: IssueState;
  first_seen_ts: number;
  last_seen_ts: number;
  resolved_ts: number | null;
  clear_streak: number;
  occurrences: number;
  ack_ts: number | null;
  snooze_until_ts: number | null;
  /** Operator suppression (Gitea #49), derived at read time by `isSuppressedNow`
   * in `format.ts`. `suppressed_ts` = when muted (null = never); `suppress_until_ts`
   * = optional expiry (null = indefinite); `suppressed_severity` = severity at
   * suppression, so an escalation past it voids the mute. Optional so a UI ahead
   * of its daemon reads an older payload as never-suppressed. */
  suppressed_ts?: number | null;
  suppress_until_ts?: number | null;
  suppressed_severity?: Severity | null;
  title: string;
  evidence: Record<string, unknown>;
  fix_state: FixState | null;
  reopened_from: number | null;
  /** Optional so a UI ahead of its daemon degrades to "not measured" rather
   * than crashing on an older `/api/issues` payload. */
  impact?: IssueImpact | null;
  /** Clear threshold + recurrence, derived server-side (Gitea #39). Optional for
   * the same reason `impact` is: an older daemon just omits it, and the list
   * falls back to a bare state pill. */
  lifecycle?: IssueLifecycle | null;
  /** Incident membership (section 17) — a join on the read model, so an issue's
   * own lifecycle is untouched. Null when the issue is in no open incident. */
  incident_id?: number | null;
  incident_role?: IncidentRole | null;
  /** Present only when this issue's incident is genuine (2+ members, Gitea
   * #21) — lets the Issues list group root + symptoms into one row with no
   * second fetch. Null for a standalone issue, even one with its own
   * incident-of-one bookkeeping row. */
  incident_brief?: IssueIncidentBrief | null;
}

export interface IssueIncidentBrief {
  id: number;
  title: string;
  summary: string;
  severity: Severity;
  /** Members other than the root; >= 1 always true when this field is present
   * at all (a null incident_brief means "not a genuine group"). */
  symptom_count: number;
}

export interface IssueEventRow {
  id: number;
  issue_id: number;
  ts: number;
  kind: string;
  detail: Record<string, unknown>;
}

export interface IssueListResponse {
  issues: IssueRow[];
  count: number;
}

/** The incident an issue is part of, for the "Part of:" link on issue detail.
 * `symptom_count === 0` means this is a genuine incident-of-one (Gitea #21):
 * real engine bookkeeping, but not a presentation-tier "incident" — the
 * detail page renders no line for it, so there is no self-link. */
export interface IssueIncidentRef {
  id: number;
  role: IncidentRole;
  title: string;
  severity: Severity;
  symptom_count: number;
}

/** One evidence key's presentation metadata (netadmin.detect.catalog.EvidenceField),
 * resolved server-side against this issue's own evidence. See EvidenceView. */
export interface EvidenceFieldLayout {
  key: string;
  label: string;
  unit: string;
  percent: boolean;
  /** Seconds -> compact duration ("10 min", "1 h") instead of a bare unit suffix. */
  duration: boolean;
}

export interface IssueDetailResponse {
  issue: IssueRow;
  entity: EntityRef | null;
  evidence: Record<string, unknown>;
  /** Narrative label/unit/order for the evidence keys the detector's catalog
   * playbook documents, in that order. Keys not listed still appear in
   * `evidence` — the UI falls back to its generic renderer for those. */
  evidence_layout: EvidenceFieldLayout[];
  confounders: string[];
  /** One narrated sentence per confounder key the playbook can explain, using
   * this issue's own evidence numbers. A key absent here has no note. */
  confounder_notes: Record<string, string>;
  events: IssueEventRow[];
  incident: IssueIncidentRef | null;
}

/* ---- Incidents (section 17) --------------------------------------------- */

export interface IncidentRootRef {
  issue_id: number;
  detector_key: string;
  title: string;
  severity: Severity;
  state: IssueState;
  entity: EntityRef | null;
}

export interface IncidentSummary {
  id: number;
  fingerprint: string;
  root_issue_id: number;
  severity: Severity;
  state: 'open' | 'resolved' | string;
  first_seen_ts: number;
  last_seen_ts: number;
  resolved_ts: number | null;
  title: string;
  summary: string;
  member_count: number;
  symptom_count: number;
  root: IncidentRootRef | null;
}

export interface IncidentListResponse {
  incidents: IncidentSummary[];
  count: number;
  /** How many incidents were dropped because ALL their members are
   * operator-suppressed (Gitea #49) — the disclosure that keeps the shrunk list
   * from being silent. Optional so an older daemon's payload still reads. */
  suppressed_excluded?: number;
}

export interface IncidentMember {
  issue: IssueRow;
  entity: EntityRef | null;
  role: IncidentRole;
  rule: string;
  rationale: string;
}

export interface IncidentDetailResponse {
  incident: IncidentSummary;
  root: IncidentMember | null;
  symptoms: IncidentMember[];
  recommended_fix: {
    issue_id: number;
    detector_key: string | null;
    fix_state: FixState | null;
  };
  investigation: { issue_id: number };
}

/* ---- Offenders (section 17) --------------------------------------------- */

export interface OffenderRow {
  entity_id: number;
  score: number;
  /** Client-axis minutes attributed to this entity — time real clients spent
   *  below a service level because of it. What `score` is built from. Never
   *  added to `down_minutes`: different unit, different population (Gitea #38). */
  fail_minutes: number;
  /** Device-axis minutes: how long this AP, switch or gateway was itself
   *  offline. `null` means not measured (a client or radio has no downtime axis
   *  at all; or the engine judged that axis nowhere in the window) — never
   *  render it as 0. Deliberately absent from `score`. */
  down_minutes: number | null;
  issue_counts: { p1: number; p2: number; p3: number; total: number };
  event_count: number;
  components: { sle_minutes: number; issues: number; events: number };
  entity: EntityRef | null;
}

export interface OffendersResponse {
  start_ts: number;
  end_ts: number;
  window_s: number;
  weights: Record<string, number>;
  /** Distinct clients the SLE engine judged in the window — the denominator
   *  every client-minute figure on this surface is quoted against. */
  clients_in_window: number;
  count: number;
  offenders: OffenderRow[];
}

export interface IssueFilterParams {
  state?: IssueState;
  severity?: Severity;
  entity_id?: number;
}

/* ---- SLE ---------------------------------------------------------------- */

export interface SlePoint {
  ts: number;
  score: number;
  ok_minutes: number;
  total_minutes: number;
}

export interface SleOffenderRow {
  attributed_entity_id: number | null;
  fail_minutes: number;
  entity: EntityRef | null;
}

export interface SleEntryRow {
  sle: string;
  score: number | null;
  total_minutes: number;
  ok_minutes: number;
  fail_minutes: number;
  /** Distinct 5-minute buckets in the window that produced any judgment for
   * this SLE (out of `window_buckets`) — the exposure the confidence floor and
   * the sparkline's gap caption are both built from (see SleHealthBlock). */
  evaluated_buckets: number;
  window_buckets: number;
  /** A real score computed from too little exposure to headline with full
   * confidence (netadmin.sle.scores.MIN_EXPOSURE_FRACTION / _MINUTES). */
  below_floor: boolean;
  /** False only for `connect` while the event pipeline itself looks dead —
   * see `unmeasurable_reason`. Never false merely because score is null. */
  measurable: boolean;
  unmeasurable_reason: string | null;
  /** score === null, but positively confirmed as "fully observed, nothing
   * happened" rather than a measurement gap (roaming/connect only). */
  quiet_pass: boolean;
  classifiers: Record<string, number>;
  top_offenders: SleOffenderRow[];
  timeseries: SlePoint[];
}

export interface SleResponse {
  start_ts: number;
  end_ts: number;
  headline: number | null;
  weights: Record<string, number>;
  window_buckets: number;
  /** Headline provenance: every SLE lands in exactly one of these three (or is
   * silently opted out via a zero-weight config, appearing in none). */
  included_sles: string[];
  excluded_below_floor: string[];
  excluded_no_data: string[];
  excluded_not_measurable: string[];
  sles: Record<string, SleEntryRow>;
}

/* ---- Metrics ------------------------------------------------------------ */

export interface MetricBucket {
  ts: number;
  min: number | null;
  max: number | null;
  avg: number | null;
  n: number;
}

export interface MetricWindowResponse {
  entity_id: number;
  metric: string;
  series_id: number;
  tier: string;
  start_ts: number;
  end_ts: number;
  seconds: number;
  points: number;
  raw_count: number;
  buckets: MetricBucket[];
}

export interface MetricWindowParams {
  entity_id: number;
  metric: string;
  seconds?: number;
  points?: number;
  end?: number;
}

/* ---- Events ------------------------------------------------------------- */

export interface NetEventRow {
  id: number;
  ts: number;
  key: string;
  msg: string | null;
  native_id: string | null;
  entity: EntityRef | null;
  related_entity: EntityRef | null;
  data: Record<string, unknown>;
}

export interface EventQueryParams {
  since_ts?: number;
  keys?: string[];
  entity_id?: number;
  limit?: number;
}

/* ---- Fetch plumbing ----------------------------------------------------- */

const BASE = import.meta.env.VITE_API_URL ?? '';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(','));
    } else {
      sp.set(k, String(v));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(),
        ...(init?.headers as Record<string, string> | undefined),
      },
    });
  } catch (cause) {
    throw new ApiError(0, `network error: ${(cause as Error).message}`);
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${res.statusText}`.trim());
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Fetch wrapper with just-in-time auth (§18.1). GET reads are open and fire
 * directly. A MUTATING call (POST/PUT/PATCH/DELETE) that 401s means the stored
 * token is absent or stale: drop it, prompt for the access token just-in-time (a
 * small modal over the live view — never a wall), and retry with the entered
 * token. The loop lets a wrong entry re-prompt; dismissing surfaces the 401 so the
 * caller can show its own error and the user stays put.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase();
  if (method === 'GET') return send<T>(path, init);

  let firstTry = true;
  for (;;) {
    try {
      return await send<T>(path, init);
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) throw e;
      if (getToken()) clearToken();
      const entered = await promptForToken(
        firstTry ? undefined : 'That token was not accepted. Try again.',
      );
      firstTry = false;
      if (!entered) throw e; // dismissed — surface the 401, stay on the page
    }
  }
}

/* ---- Endpoint wrappers -------------------------------------------------- */

export const listIssues = (filter: IssueFilterParams = {}) =>
  request<IssueListResponse>(`/api/issues${qs({ ...filter })}`);

export const getIssue = (id: number) =>
  request<IssueDetailResponse>(`/api/issues/${id}`);

/* ---- Incidents + offenders (section 17) --------------------------------- */

/** `includeSingletons` restores the engine's uniform one-row-per-root
 * projection (every incident-of-one included); the default is genuine groups
 * only (2+ members, Gitea #21). The dashboard's "Needs attention" card passes
 * `true` for the honest, everything-open-work triage view. */
export const listIncidents = (includeResolved = false, includeSingletons = false) =>
  request<IncidentListResponse>(
    `/api/incidents${qs({
      include_resolved: includeResolved ? true : undefined,
      include_singletons: includeSingletons ? true : undefined,
    })}`,
  );

export const getIncident = (id: number) =>
  request<IncidentDetailResponse>(`/api/incidents/${id}`);

export const listDeviceOffenders = (windowS?: number, topN?: number) =>
  request<OffendersResponse>(`/api/devices/offenders${qs({ window_s: windowS, top_n: topN })}`);

export const listClientOffenders = (windowS?: number, topN?: number) =>
  request<OffendersResponse>(`/api/clients/offenders${qs({ window_s: windowS, top_n: topN })}`);

export const ackIssue = (id: number) =>
  request<{ issue: IssueRow }>(`/api/issues/${id}/ack`, { method: 'POST' }).then(
    (r) => r.issue,
  );

export const snoozeIssue = (id: number, untilTs: number) =>
  request<{ issue: IssueRow }>(`/api/issues/${id}/snooze`, {
    method: 'POST',
    body: JSON.stringify({ until_ts: untilTs }),
  }).then((r) => r.issue);

/** Suppress an issue: park its claim on attention (counts, alerts) with an
 * optional expiry (Gitea #49). `untilTs` omitted = "until I unsuppress".
 * Measured impact is untouched. Subsumes snooze — this is the one mute. */
export const suppressIssue = (id: number, untilTs?: number) =>
  request<{ issue: IssueRow }>(`/api/issues/${id}/suppress`, {
    method: 'POST',
    body: JSON.stringify({ until_ts: untilTs ?? null }),
  }).then((r) => r.issue);

/** Lift an operator suppression: the issue re-enters counts and alerts. */
export const unsuppressIssue = (id: number) =>
  request<{ issue: IssueRow }>(`/api/issues/${id}/unsuppress`, { method: 'POST' }).then(
    (r) => r.issue,
  );

/* ---- Fixes (ARCHITECTURE.md §9) ---------------------------------------- */

export type FixRisk = 'low' | 'medium' | 'high';
export type FixVerificationStatus =
  | 'not_armed'
  | 'pending'
  | 'verified'
  | 'failed'
  | 'expired';

export interface FixStepDiff {
  before: unknown;
  after: unknown;
}

export interface FixPlanStep {
  action: string;
  method: string;
  endpoint: string;
  payload: Record<string, unknown>;
  description: string;
  risk: FixRisk | string;
  target: string;
  revertible: boolean;
  precondition: {
    target: string;
    expected: Record<string, unknown>;
    description: string;
  };
  diff: Record<string, FixStepDiff>;
}

export interface FixVerification {
  status: FixVerificationStatus;
  armed_ts: number | null;
  window_end_ts: number | null;
  resolved_ts: number | null;
}

export interface FixChange {
  id: number;
  ts: number;
  issue_id: number | null;
  action: string;
  status: 'applied' | 'reverted' | 'failed' | string;
  reverted_ts: number | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  revertible: boolean;
  /** The device this change touched. A joint band re-plan ledgers one row per
   *  radio moved, so the card must name it or every Revert looks identical. */
  entity_id: number | null;
  entity_name: string | null;
  entity_native_id: string | null;
}

export interface FixPlanResponse {
  issue_id: number;
  detector_key: string;
  title: string;
  entity_native_id: string;
  advisory: string | null;
  manual_action_required: boolean;
  confirm_token: string;
  device_count: number;
  steps: FixPlanStep[];
  fix_state: FixState | null;
  verification: FixVerification;
  changes: FixChange[];
}

export interface FixApplyStep {
  action: string;
  status: string;
  change_id: number | null;
  status_code: number | null;
  error: string | null;
}

export interface FixApplyResponse {
  issue_id: number;
  applied: boolean;
  aborted_reason: string | null;
  change_ids: number[];
  steps: FixApplyStep[];
  fix_state: FixState | null;
  verification: FixVerification;
  changes: FixChange[];
}

export interface FixHistoryResponse {
  issue_id: number;
  fix_state: FixState | null;
  verification: FixVerification;
  changes: FixChange[];
}

/** Already-applied changes + verification, store-only (no device read, ever) --
 * safe to fetch unconditionally on issue-detail load, unlike `getFixPlan`. */
export const getFixHistory = (issueId: number) =>
  request<FixHistoryResponse>(`/api/issues/${issueId}/fix-history`);

/** The dry-run plan for an issue: exact calls, confirm token, verification. */
export const getFixPlan = (issueId: number) =>
  request<FixPlanResponse>(`/api/issues/${issueId}/fix-plan`);

/** Apply a reviewed plan. `confirmToken` must match the plan the user read. */
export const applyFix = (issueId: number, confirmToken: string) =>
  request<FixApplyResponse>(`/api/issues/${issueId}/fix/apply`, {
    method: 'POST',
    body: JSON.stringify({ confirm: true, confirm_token: confirmToken }),
  });

/** Revert a change from this issue's ledger, restoring its before-state. */
export const revertFix = (issueId: number, changeId: number) =>
  request<{ change: FixChange | null; verification: FixVerification }>(
    `/api/issues/${issueId}/fix/revert`,
    { method: 'POST', body: JSON.stringify({ change_id: changeId }) },
  );

export const getSle = (windowS?: number, buckets?: number) =>
  request<SleResponse>(`/api/sle${qs({ window_s: windowS, buckets })}`);

export const getMetricWindow = (p: MetricWindowParams) =>
  request<MetricWindowResponse>(`/api/metrics/window${qs({ ...p })}`);

export const listEvents = (q: EventQueryParams = {}) =>
  request<{ events: NetEventRow[]; count: number }>(`/api/events${qs({ ...q })}`);
