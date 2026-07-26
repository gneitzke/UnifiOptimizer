/**
 * Inventory data layer for the device + client drill-downs.
 *
 * These shapes are transcribed from the ACTUAL backend responses
 * (netadmin/server/routers/inventory.py), which are the source of truth. They
 * intentionally differ from the aspirational shapes in src/api/types.ts —
 * notably: rollups key entity identity as `entity_id`/`type` (not `id`), `state`
 * is a string→string map, `metrics` is a LIST of samples (not a dict), and the
 * detail splits issues into `issues_open` / `issues_resolved`. When the shared
 * api client is reconciled at integrate-time this module should fold into it.
 */

import { ApiError } from '../../api';
import { authHeaders, markAuthRequired } from '../../api/token';

const BASE = import.meta.env.VITE_API_URL ?? '';

async function apiGet<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { Accept: 'application/json', ...authHeaders() },
    });
  } catch (cause) {
    throw new ApiError(0, `network error: ${(cause as Error).message}`);
  }
  if (!res.ok) {
    if (res.status === 401) markAuthRequired();
    const body = await res.text().catch(() => '');
    throw new ApiError(res.status, `${res.status} ${res.statusText}`.trim(), body);
  }
  return (await res.json()) as T;
}

/* ---- Shared entity shapes ---------------------------------------------- */

export type Severity = 'p1' | 'p2' | 'p3';
export type IssueState = 'pending' | 'active' | 'resolving' | 'resolved';
export type DeviceType = 'ap' | 'switch' | 'gateway';

export interface Sample {
  metric: string;
  unit: string | null;
  ts: number;
  value: number | null;
}

export interface IssueCounts {
  p1: number;
  p2: number;
  p3: number;
  total: number;
}

export interface StateChange {
  id: number;
  entity_id: number;
  attr: string;
  old_value: string | null;
  new_value: string | null;
  ts: number;
}

/** An issue row as the inventory detail serialises it (no resolved entity ref). */
export interface InvIssue {
  id: number;
  detector_key: string;
  entity_id: number | null;
  severity: Severity;
  state: IssueState;
  first_seen_ts: number;
  last_seen_ts: number;
  resolved_ts: number | null;
  occurrences: number;
  ack_ts: number | null;
  snooze_until_ts: number | null;
  title: string;
  evidence: Record<string, unknown>;
  fix_state: string | null;
}

export interface EntityBase {
  entity_id: number;
  native_id: string;
  name: string;
  type: string;
  model: string | null;
  parent_id: number | null;
  first_seen_ts: number;
  last_seen_ts: number;
  meta: Record<string, unknown>;
  state: Record<string, string | null>;
  metrics: Sample[];
}

/* ---- Devices ------------------------------------------------------------ */

export interface DeviceRollup extends EntityBase {
  type: DeviceType;
  issue_counts: IssueCounts;
}

export interface ChildEntity extends EntityBase {
  type: 'radio' | 'port';
}

export interface DeviceDetail extends EntityBase {
  type: DeviceType;
  state_changes: StateChange[];
  children: ChildEntity[];
  issues_open: InvIssue[];
  issues_resolved: InvIssue[];
}

export const listDevices = () =>
  apiGet<{ devices: DeviceRollup[]; count: number }>('/api/inventory/devices');

export const getDevice = (id: number) =>
  apiGet<{ device: DeviceDetail }>(`/api/inventory/devices/${id}`).then((r) => r.device);

/* ---- Clients ------------------------------------------------------------ */

export interface ClientRollup extends EntityBase {
  type: 'client';
  issue_counts: IssueCounts;
}

export interface EntityRef {
  entity_id: number;
  name: string;
  type: string;
  native_id: string;
  model: string | null;
}

export interface JourneyEvent {
  id: number;
  ts: number;
  key: string;
  msg: string | null;
  related_entity: EntityRef | null;
  data: Record<string, unknown>;
}

export interface ClientDetail extends EntityBase {
  type: 'client';
  state_changes: StateChange[];
  issues_open: InvIssue[];
  issues_resolved: InvIssue[];
  journey: JourneyEvent[];
  current_ap: EntityRef | null;
}

export const listClients = () =>
  apiGet<{ clients: ClientRollup[]; count: number }>('/api/inventory/clients');

export const getClient = (id: number) =>
  apiGet<{ client: ClientDetail }>(`/api/inventory/clients/${id}`).then((r) => r.client);

/* ---- Metric window (charts) -------------------------------------------- */

export interface WindowBucket {
  ts: number;
  min: number;
  max: number;
  avg: number;
  n: number;
}

export interface MetricWindow {
  entity_id: number;
  metric: string;
  series_id: number;
  tier: string;
  start_ts: number;
  end_ts: number;
  seconds: number;
  points: number;
  raw_count: number;
  buckets: WindowBucket[];
}

export const getMetricWindow = (
  entityId: number,
  metric: string,
  seconds: number,
  points = 240,
) =>
  apiGet<MetricWindow>(
    `/api/metrics/window?entity_id=${entityId}&metric=${encodeURIComponent(
      metric,
    )}&seconds=${seconds}&points=${points}`,
  );

/* ---- SLE (per-client minutes) ------------------------------------------ */

export interface SleOffender {
  attributed_entity_id: number;
  fail_minutes: number;
  entity: EntityRef | null;
}

export interface SleEntry {
  sle: string;
  score: number | null;
  total_minutes: number;
  ok_minutes: number;
  fail_minutes: number;
  classifiers: Record<string, number>;
  top_offenders: SleOffender[];
}

export interface SleReport {
  start_ts: number;
  end_ts: number;
  headline: number | null;
  weights: Record<string, number>;
  sles: Record<string, SleEntry>;
}

export const getSle = (windowS?: number) =>
  apiGet<SleReport>(`/api/sle${windowS ? `?window_s=${windowS}` : ''}`);
