/**
 * Typed fetch wrapper for the netadmin API (docs/ARCHITECTURE.md §12).
 *
 * These are read-only GETs backing the shell (health, issue summary) and the
 * timeline/changes surfaces. The mutating routes (ack, snooze) live in the
 * page-owned data layer and touch only OUR database, never the controller.
 * Requests go through the Vite dev proxy to the snapshot test server, so the
 * base URL is empty in the browser.
 */

import { authHeaders, markAuthRequired } from './token';
import type {
  ChangeRecord,
  EventQuery,
  Health,
  IssueFilter,
  IssueList,
  NetEvent,
} from './types';

const BASE = import.meta.env.VITE_API_URL ?? '';

export class ApiError extends Error {
  readonly status: number;
  readonly body?: string;

  constructor(status: number, message: string, body?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...authHeaders(),
        ...(init.headers as Record<string, string> | undefined),
      },
    });
  } catch (cause) {
    // Network failure / server down — a first-class state, not a thrown 500.
    throw new ApiError(0, `network error: ${(cause as Error).message}`);
  }

  if (!res.ok) {
    if (res.status === 401) markAuthRequired();
    const body = await res.text().catch(() => '');
    throw new ApiError(res.status, `${res.status} ${res.statusText}`.trim(), body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/* ---- System ------------------------------------------------------------- */

export const getHealth = () => request<Health>('/api/health');

/* ---- Issues ------------------------------------------------------------- */

// The issue list backs the shell's sidebar summary + the live activity ticker.
// Issue *detail*, ack/snooze, SLE, inventory and metric-window fetchers live in
// the page-owned data layers (`pages/shared/api.ts`, `pages/devices/api.ts`),
// which are transcribed against the actual router shapes; this module keeps only
// the endpoints the shell + timeline/changes surfaces consume, so each endpoint
// has a single fetcher home rather than a drifted duplicate here.
export const listIssues = (filter: IssueFilter = {}) =>
  request<IssueList>(`/api/issues${qs({ ...filter })}`);

/* ---- Events ------------------------------------------------------------- */

export const listEvents = (q: EventQuery = {}) =>
  request<{ events: NetEvent[]; count: number }>(`/api/events${qs({ ...q })}`);

/* ---- Changes ------------------------------------------------------------ */

export const listChanges = (scope: { issue_id?: number; entity_id?: number } = {}) =>
  request<{ changes: ChangeRecord[]; count: number }>(
    `/api/changes${qs({ ...scope })}`,
  );
