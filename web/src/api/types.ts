/**
 * Types for the netadmin API contract (docs/ARCHITECTURE.md §12).
 *
 * The backend is the source of truth on drift; where a Phase-3 endpoint is not
 * yet finalized these shapes follow the contract and keep uncertain fields
 * optional, so a page can read what exists without a type break.
 */

export type Severity = 'p1' | 'p2' | 'p3';
export type IssueState = 'pending' | 'active' | 'resolving' | 'resolved';
export type FixState = 'proposed' | 'applied' | 'verified' | 'failed';
export type EntityType =
  | 'ap'
  | 'switch'
  | 'gateway'
  | 'client'
  | 'port'
  | 'radio'
  | 'wlan';

/**
 * Compact entity reference the backend embeds wherever a payload points at an
 * entity (event subject, change target, SLE offender). `name` falls back to the
 * native id (a MAC) when the controller never named the entity. Source of truth:
 * `netadmin/server/serialize.py::entity_ref`.
 */
export interface EntityRef {
  entity_id: number;
  name: string | null;
  type: EntityType | string | null;
  native_id: string | null;
  model: string | null;
}

/* ---- Issues ------------------------------------------------------------- */

export interface Issue {
  id: number;
  fingerprint: string;
  detector_key: string;
  entity_id: number | null;
  /** Resolved entity name (contract: list "with entity names resolved"). */
  entity_name?: string | null;
  entity_type?: EntityType | null;
  severity: Severity;
  state: IssueState;
  first_seen_ts: number;
  last_seen_ts: number;
  resolved_ts: number | null;
  clear_streak: number;
  occurrences: number;
  ack_ts: number | null;
  snooze_until_ts: number | null;
  title: string;
  evidence: Record<string, unknown>;
  fix_state: FixState | null;
  reopened_from: number | null;
}

export interface IssueEvent {
  id: number;
  issue_id: number;
  ts: number;
  kind: string; // detected | escalated | acked | snoozed | fix_* | resolved | reopened | investigated
  detail: Record<string, unknown>;
}

export interface IssueList {
  issues: Issue[];
  count: number;
}

export interface IssueDetail {
  issue: Issue;
  events: IssueEvent[];
  /** Evidence is carried on the issue too; surfaced here when the backend splits it out. */
  evidence?: Record<string, unknown>;
  confounders?: string[];
}

export interface IssueFilter {
  state?: IssueState;
  severity?: Severity;
  entity_id?: number;
}

/* ---- Health (/api/health) ---------------------------------------------- */

export type HealthStatus = 'ok' | 'degraded' | 'starting';

export interface JobHealth {
  job: string;
  interval_s: number | null;
  last_success_ts: number | null;
  last_success_age_s: number | 'UNKNOWN';
  consecutive_failures: number;
  status: 'ok' | 'stale' | 'failing' | 'UNKNOWN';
}

export interface Health {
  status: HealthStatus;
  ready: boolean;
  uptime_s: number;
  now: number;
  db: { path: string; size_bytes: number | null };
  entities: { total: number | 'UNKNOWN'; by_type: Record<string, number> };
  jobs: JobHealth[];
  websocket: { state: string; detail?: string };
  components: Record<string, string>;
  backfill: string;
}

/* ---- SLE (/api/sle) ----------------------------------------------------- */

export interface SleOffender {
  entity_id: number;
  name?: string | null;
  fail_minutes: number;
}

export interface SleBucket {
  ts: number;
  score: number | null;
}

export interface SleEntry {
  sle: string;
  score: number | null;
  total_minutes: number;
  ok_minutes: number;
  fail_minutes: number;
  classifiers: Record<string, number>;
  top_offenders: SleOffender[];
  /** Contract extension: per-SLE timeseries buckets for the dashboard charts. */
  timeseries?: SleBucket[];
}

export interface SleReport {
  start_ts: number;
  end_ts: number;
  headline: number | null;
  weights: Record<string, number>;
  sles: Record<string, SleEntry>;
}

/* ---- Inventory (/api/inventory/*) -------------------------------------- */

export interface IssueCounts {
  p1: number;
  p2: number;
  p3: number;
}

export interface DeviceRollup {
  id: number;
  entity_type: 'ap' | 'switch' | 'gateway';
  name: string;
  model: string | null;
  state: string;
  firmware: string | null;
  issue_counts: IssueCounts;
  metrics: Record<string, number | null>;
}

export interface ChildMetricRow {
  id: number;
  entity_type: EntityType;
  name: string;
  metrics: Record<string, number | null>;
}

export interface StateChange {
  attr: string;
  old_value: string | null;
  new_value: string | null;
  ts: number;
}

export interface DeviceDetail {
  device: DeviceRollup;
  meta: Record<string, unknown>;
  state_changes: StateChange[];
  children: ChildMetricRow[];
  open_issues: Issue[];
  resolved_issues: Issue[];
}

export interface ClientRollup {
  id: number;
  name: string;
  mac?: string | null;
  state: string;
  is_wired?: boolean;
  issue_counts: IssueCounts;
  metrics: Record<string, number | null>;
}

export interface ClientDetail {
  client: ClientRollup;
  meta: Record<string, unknown>;
  state_changes: StateChange[];
  open_issues: Issue[];
  resolved_issues: Issue[];
}

/* ---- Metrics (/api/metrics/window) ------------------------------------- */

/** A downsampled bucket. A gap is a missing bucket or a null aggregate —
 * charts render it as a gap, never interpolated (never-do rule 8). */
export interface MetricPoint {
  ts: number;
  min: number | null;
  max: number | null;
  avg: number | null;
}

export interface MetricWindow {
  entity_id: number;
  metric: string;
  unit?: string | null;
  seconds: number;
  points: MetricPoint[];
}

export interface MetricWindowQuery {
  entity_id: number;
  metric: string;
  seconds?: number;
  points?: number;
}

/* ---- Events (/api/events) ---------------------------------------------- */

/**
 * A normalized controller event (`netadmin/server/routers/events.py`). Subject
 * and related entity arrive pre-resolved to name refs; `data` is the decoded
 * JSON blob. `key` is a UniFi event key, `EVT_<FAMILY>_<Name>`.
 */
export interface NetEvent {
  id: number;
  ts: number;
  key: string;
  msg: string | null;
  native_id: string | null;
  entity: EntityRef | null;
  related_entity: EntityRef | null;
  data: Record<string, unknown>;
}

export interface EventQuery {
  since_ts?: number;
  keys?: string[];
  entity_id?: number;
  limit?: number;
}

/* ---- Changes (/api/changes) -------------------------------------------- */

/**
 * A row of the config-change ledger (`netadmin/server/routers/changes.py`).
 * `before`/`after` arrive decoded to objects; `entity` is a resolved name ref.
 * Revert is a Phase-4 write — this surface is read-only.
 */
export interface ChangeRecord {
  id: number;
  ts: number;
  issue_id: number | null;
  action: string;
  status: 'applied' | 'reverted' | 'failed';
  reverted_ts: number | null;
  entity: EntityRef | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}

/* ---- WebSocket frames (/ws) -------------------------------------------- */

export interface IssueTransitionFrame {
  type: 'issue_transition';
  issue_id: number;
  from_state?: IssueState | null;
  to_state?: IssueState;
  severity?: Severity;
  title?: string;
  ts?: number;
  [key: string]: unknown;
}

export interface HeartbeatFrame {
  type: 'heartbeat';
  ts: number;
  /** Per-job last-poll ages, seconds. */
  last_poll_ages?: Record<string, number>;
  [key: string]: unknown;
}

export type WsFrame = IssueTransitionFrame | HeartbeatFrame;
