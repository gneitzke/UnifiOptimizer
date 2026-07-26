/**
 * Data layer for the tech-visit surface (`/visit`, docs/ARCHITECTURE.md §3 & §12).
 *
 * Two calls: POST /api/visit kicks a background run; GET /api/visit polls it and
 * returns the resulting VisitReport once done. Shapes mirror
 * `netadmin/visit/runner.py::VisitReport` and `server/routers/ondemand.py`.
 */

import { authHeaders, clearToken, getToken, promptForToken } from '../../api/token';
import { ApiError, type EntityRef } from '../shared/api';

const BASE = import.meta.env.VITE_API_URL ?? '';

export type VisitStatus = 'idle' | 'running' | 'done' | 'failed';
export type StepStatus = 'pending' | 'running' | 'ok' | 'failed' | 'skipped';

export interface VisitStep {
  id: string;
  label: string;
  status: StepStatus;
  detail: string | null;
  duration_ms: number | null;
}

export interface VisitIssue {
  id: number;
  detector_key: string;
  entity_id: number | null;
  entity: EntityRef | null;
  severity: 'p1' | 'p2' | 'p3';
  state: string;
  first_seen_ts: number;
  title: string;
  evidence: Record<string, unknown>;
  confounders: string[];
}

export interface VisitSleOffender {
  attributed_entity_id: number | null;
  fail_minutes: number;
  entity: EntityRef | null;
}

export interface VisitSleEntry {
  sle: string;
  score: number | null;
  total_minutes: number;
  ok_minutes: number;
  fail_minutes: number;
  classifiers: Record<string, number>;
  top_offenders: VisitSleOffender[];
}

export interface VisitCoverage {
  job: string;
  interval_s: number;
  live: number | null;
  backfill: number | null;
  total: number | null;
}

export interface VisitReport {
  started_ts: number;
  finished_ts: number;
  window_start_ts: number;
  window_end_ts: number;
  site_id: string;
  lookback_days: number;
  controller_host: string | null;
  headline_score: number | null;
  sles: { sles: Record<string, VisitSleEntry> };
  issues: VisitIssue[];
  issue_counts: { total: number; p1: number; p2: number; p3: number; open: number };
  topology: { entity_count: number; by_type: Record<string, number>; devices: EntityRef[] };
  coverage: VisitCoverage[];
  caveats: string[];
  steps: VisitStep[];
  db_path: string | null;
}

export interface VisitRunSnapshot {
  run_id: string | null;
  status: VisitStatus;
  started_ts?: number;
  finished_ts?: number | null;
  lookback_days?: number | null;
  steps: VisitStep[];
  report: VisitReport | null;
  error?: string | null;
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
  return (await res.json()) as T;
}

/** Fetch wrapper with just-in-time auth (§18.1): a mutating call that 401s
 *  prompts for the access token over the live view and retries. See
 *  `pages/shared/api.ts` for the shared rationale. */
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
      if (!entered) throw e;
    }
  }
}

export const getVisit = () => request<VisitRunSnapshot>('/api/visit');

export const startVisit = (lookbackDays?: number) =>
  request<VisitRunSnapshot>('/api/visit', {
    method: 'POST',
    body: JSON.stringify(lookbackDays ? { lookback_days: lookbackDays } : {}),
  });
