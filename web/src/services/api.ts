import type {
  LoginRequest,
  LoginResponse,
  AuthStatus,
  AnalysisJob,
  AnalysisResult,
  ChangePreview,
  ChangeResult,
  ChangeHistoryEntry,
  DiscoveredDevice,
} from '../types/api';

const BASE = import.meta.env.VITE_API_URL ?? '';
const TOKEN_KEY = 'unifi_token';

/* ── helpers ───────────────────────────────────── */

function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string | null): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init.headers as Record<string, string>),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers,
  });

  if (res.status === 401) {
    setToken(null);
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/* ── Auth ──────────────────────────────────────── */

export async function login(
  req: LoginRequest,
): Promise<LoginResponse> {
  const data = await request<LoginResponse>(
    '/api/auth/login',
    { method: 'POST', body: JSON.stringify(req) },
  );
  setToken(data.token);
  return data;
}

export async function logout(): Promise<void> {
  await request<void>(
    '/api/auth/logout',
    { method: 'POST' },
  ).catch(() => {});
  setToken(null);
}

export async function validate(): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/status');
}

/* ── Discovery ─────────────────────────────────── */

export async function discover(): Promise<
  DiscoveredDevice[]
> {
  const res = await request<{
    devices: DiscoveredDevice[];
    scan_duration_ms: number;
  }>('/api/auth/discover', { method: 'POST' });
  return res.devices;
}

/* ── Analysis ──────────────────────────────────── */

export async function runAnalysis(): Promise<
  AnalysisJob
> {
  const raw = await request<Record<string, unknown>>(
    '/api/analysis/run',
    {
      method: 'POST',
      body: JSON.stringify({}),
    },
  );
  return {
    jobId: raw.job_id as string,
    status: raw.status as AnalysisJob['status'],
    progress: raw.progress as number,
    startedAt: (raw.started_at as string) ?? '',
    completedAt: raw.completed_at as string | undefined,
    error: raw.error as string | undefined,
  };
}

function mapJob(raw: Record<string, unknown>): AnalysisJob {
  return {
    jobId: (raw.job_id ?? raw.jobId) as string,
    status: raw.status as AnalysisJob['status'],
    progress: raw.progress as number,
    startedAt: (raw.started_at as string) ?? '',
    completedAt: raw.completed_at as string | undefined,
    error: raw.error as string | undefined,
  };
}

export async function getAnalysisStatus(
  jobId: string,
): Promise<AnalysisJob> {
  const raw = await request<Record<string, unknown>>(
    `/api/analysis/status/${jobId}`,
  );
  return mapJob(raw);
}

export async function getAnalysisResults(
  jobId: string,
): Promise<AnalysisResult> {
  return request<AnalysisResult>(
    `/api/analysis/results/${jobId}`,
  );
}

/* ── Repair ────────────────────────────────────── */

export async function previewRepair(
  jobId: string,
): Promise<ChangePreview[]> {
  return request<ChangePreview[]>(
    `/api/repair/${jobId}/preview`,
  );
}

export async function applyRepair(
  changeId: string,
): Promise<ChangeResult> {
  return request<ChangeResult>(
    `/api/repair/${changeId}/apply`,
    { method: 'POST' },
  );
}

export async function revertChange(
  changeId: string,
): Promise<ChangeResult> {
  return request<ChangeResult>(
    `/api/repair/${changeId}/revert`,
    { method: 'POST' },
  );
}

export async function getChangeHistory(): Promise<
  ChangeHistoryEntry[]
> {
  return request<ChangeHistoryEntry[]>(
    '/api/repair/history',
  );
}
