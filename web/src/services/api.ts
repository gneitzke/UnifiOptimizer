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
  HealthScore,
  ApAnalysis,
  ClientAnalysis,
  Finding,
} from '../types/api';

const BASE = import.meta.env.VITE_API_URL ?? '';
const TOKEN_KEY = 'unifi_token';
const CREDS_KEY = 'unifi_creds';

/* ── helpers ───────────────────────────────────── */

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string | null): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getStoredCreds(): {
  host: string;
  username: string;
  site: string;
} | null {
  try {
    const raw = localStorage.getItem(CREDS_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setStoredCreds(
  host: string,
  username: string,
  site: string,
): void {
  localStorage.setItem(
    CREDS_KEY,
    JSON.stringify({ host, username, site }),
  );
}

export function clearStoredCreds(): void {
  localStorage.removeItem(CREDS_KEY);
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
  const raw = await request<Record<string, unknown>>(
    '/api/auth/login',
    { method: 'POST', body: JSON.stringify(req) },
  );
  const data: LoginResponse = {
    token: raw.token as string,
    host: (raw.host as string) ?? req.host,
    site: (raw.site as string) ?? req.site ?? 'default',
    username: (raw.username as string) ?? req.username,
    expiresAt: raw.expires_in
      ? new Date(Date.now() + (raw.expires_in as number) * 1000).toISOString()
      : '',
  };
  setToken(data.token);
  setStoredCreds(data.host, data.username, data.site);
  return data;
}

export async function logout(): Promise<void> {
  await request<void>(
    '/api/auth/logout',
    { method: 'POST' },
  ).catch(() => {});
  setToken(null);
  clearStoredCreds();
}

export async function validate(): Promise<AuthStatus> {
  return request<AuthStatus>('/api/auth/status');
}

/* ── Discovery ─────────────────────────────────── */

export async function discover(
  subnet?: string,
): Promise<DiscoveredDevice[]> {
  const qs = subnet
    ? `?subnet=${encodeURIComponent(subnet)}`
    : '';
  const res = await request<{
    devices: DiscoveredDevice[];
    scan_duration_ms: number;
  }>(`/api/auth/discover${qs}`, {
    method: 'POST',
  });
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
  return mapJob(raw);
}

function mapJob(raw: Record<string, unknown>): AnalysisJob {
  return {
    jobId: (raw.job_id ?? raw.jobId) as string,
    status: raw.status as AnalysisJob['status'],
    progress: raw.progress as number,
    startedAt: (raw.started_at as string) ?? '',
    completedAt: raw.completed_at as string | undefined,
    error: (raw.message as string) || (raw.error as string) || undefined,
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

function mapAnalysisResult(
  raw: Record<string, unknown>,
): AnalysisResult {
  const fullAnalysis = (raw.full_analysis ?? {}) as Record<string, unknown>;
  const healthRaw = (raw.health_score ?? fullAnalysis.health_score ?? {}) as Record<string, unknown>;
  const apRaw = (raw.ap_analysis ?? fullAnalysis.ap_analysis ?? {}) as Record<string, unknown>;
  const clientRaw = (raw.client_analysis ?? fullAnalysis.client_analysis ?? {}) as Record<string, unknown>;
  const recsRaw = (raw.recommendations ?? []) as Record<string, unknown>[];

  const health: HealthScore = {
    overall: (healthRaw.overall_score ?? healthRaw.overall ?? 0) as number,
    wireless: (healthRaw.wireless_score ?? healthRaw.wireless ?? 0) as number,
    wired: (healthRaw.wired_score ?? healthRaw.wired ?? 0) as number,
    latency: (healthRaw.latency_score ?? healthRaw.latency ?? 0) as number,
    coverage: (healthRaw.coverage_score ?? healthRaw.coverage ?? 0) as number,
  };

  const apList = (apRaw.aps ?? apRaw.devices ?? []) as Record<string, unknown>[];
  const aps: ApAnalysis[] = apList.map((a) => ({
    mac: (a.mac ?? '') as string,
    name: (a.name ?? a.hostname ?? 'Unknown') as string,
    model: (a.model ?? '') as string,
    channel: (a.channel ?? 0) as number,
    band: (a.band ?? a.radio ?? '') as string,
    txPower: (a.tx_power ?? a.txPower ?? 0) as number,
    clients: (a.num_sta ?? a.clients ?? 0) as number,
    satisfaction: (a.satisfaction ?? a.score ?? 0) as number,
    interference: (a.interference ?? 0) as number,
    issues: (a.issues ?? []) as string[],
    suggestions: (a.suggestions ?? []) as string[],
  }));

  const clientList = (clientRaw.clients ?? clientRaw.problem_clients ?? []) as Record<string, unknown>[];
  const clients: ClientAnalysis[] = clientList.map((c) => ({
    mac: (c.mac ?? '') as string,
    hostname: (c.hostname ?? c.name ?? '') as string,
    apMac: (c.ap_mac ?? c.apMac ?? '') as string,
    signal: (c.signal ?? c.rssi ?? 0) as number,
    noise: (c.noise ?? 0) as number,
    txRate: (c.tx_rate ?? c.txRate ?? 0) as number,
    rxRate: (c.rx_rate ?? c.rxRate ?? 0) as number,
    satisfaction: (c.satisfaction ?? 0) as number,
    issues: (c.issues ?? []) as string[],
  }));

  const findings: Finding[] = recsRaw.map((r, i) => ({
    id: String(r.id ?? i),
    severity: (r.severity ?? r.priority ?? 'info') as Finding['severity'],
    category: (r.category ?? r.action ?? 'general') as string,
    title: (r.title ?? r.reason ?? r.action ?? 'Recommendation') as string,
    description: (r.description ?? r.details ?? r.reason ?? '') as string,
    affectedDevices: (r.affected_devices ?? []) as string[],
  }));

  return {
    jobId: (raw.job_id ?? '') as string,
    timestamp: new Date().toISOString(),
    health,
    aps,
    clients,
    apCount: aps.length,
    clientCount: clients.length,
    summary: `${aps.length} APs, ${clients.length} problem clients, ${findings.length} recommendations`,
    findings,
  };
}

export async function getAnalysisResults(
  jobId: string,
): Promise<AnalysisResult> {
  const raw = await request<Record<string, unknown>>(
    `/api/analysis/results/${jobId}`,
  );
  return mapAnalysisResult(raw);
}

/* ── Repair ────────────────────────────────────── */

export async function previewRepair(
  jobId: string,
  recommendationIds: number[],
): Promise<{ previews: ChangePreview[] }> {
  const raw = await request<{ previews: Record<string, unknown>[] }>(
    `/api/repair/preview?job_id=${encodeURIComponent(jobId)}`,
    {
      method: 'POST',
      body: JSON.stringify({
        recommendation_ids: recommendationIds,
      }),
    },
  );
  const previews: ChangePreview[] = (raw.previews ?? []).map((p) => {
    const impact = (p.impact ?? {}) as Record<string, unknown>;
    const action = (p.action ?? '') as string;
    const actionLabels: Record<string, string> = {
      channel_change: 'Channel Change',
      power_change: 'TX Power Change',
      band_steering: 'Band Steering',
      min_rssi: 'Minimum RSSI',
    };
    return {
      changeId: String(p.index ?? ''),
      description: (impact.reason as string) || actionLabels[action] || action,
      deviceMac: '',
      deviceName: (p.device_name ?? '') as string,
      setting: actionLabels[action] || action,
      currentValue: (p.current_value ?? '') as string,
      proposedValue: (p.new_value ?? '') as string,
      risk: (impact.risk_level ?? 'medium') as ChangePreview['risk'],
    };
  });
  return { previews };
}

export async function applyRepair(
  jobId: string,
  recommendationIds: number[],
  dryRun = false,
): Promise<{
  results: ChangeResult[];
  summary: { applied: number; failed: number; skipped: number; dry_run: boolean };
}> {
  const raw = await request<{
    results: Record<string, unknown>[];
    summary: { applied: number; failed: number; skipped: number; dry_run: boolean };
  }>(
    `/api/repair/apply?job_id=${encodeURIComponent(jobId)}`,
    {
      method: 'POST',
      body: JSON.stringify({
        recommendation_ids: recommendationIds,
        dry_run: dryRun,
      }),
    },
  );
  return {
    results: (raw.results ?? []).map((r) => ({
      changeId: r.recommendation_index != null ? String(r.recommendation_index) : (r.change_id ?? '') as string,
      realChangeId: (r.change_id ?? '') as string,
      success: r.status === 'applied' || r.status === 'dry_run',
      appliedAt: (r.timestamp ?? new Date().toISOString()) as string,
      revertible: r.status === 'applied',
      error: (r.error ?? undefined) as string | undefined,
    })),
    summary: raw.summary,
  };
}

export async function revertChange(
  changeId: string,
): Promise<{ reverted: boolean; change_id: string }> {
  return request(
    '/api/repair/revert',
    {
      method: 'POST',
      body: JSON.stringify({ change_id: changeId }),
    },
  );
}

export async function getChangeHistory(): Promise<
  ChangeHistoryEntry[]
> {
  const raw = await request<{ changes: Record<string, unknown>[] }>(
    '/api/repair/history',
  );
  return (raw.changes ?? []).map(mapChangeEntry);
}

export async function getRevertableChanges(): Promise<
  ChangeHistoryEntry[]
> {
  const raw = await request<{ changes: Record<string, unknown>[] }>(
    '/api/repair/revertable',
  );
  return (raw.changes ?? []).map(mapChangeEntry);
}

function mapChangeEntry(
  h: Record<string, unknown>,
): ChangeHistoryEntry {
  const beforeConfig = h.before_config as Record<string, unknown> | undefined;
  const afterConfig = h.after_config as Record<string, unknown> | undefined;
  const prevVal = beforeConfig
    ? JSON.stringify(beforeConfig).slice(0, 100)
    : '';
  const newVal = afterConfig
    ? (afterConfig.action as string ?? JSON.stringify(afterConfig).slice(0, 100))
    : '';
  return {
    changeId: (h.change_id ?? h.changeId ?? '') as string,
    description: (h.action ?? h.description ?? '') as string,
    deviceName: (h.device_name ?? h.deviceName ?? '') as string,
    setting: (h.action ?? h.setting ?? '') as string,
    previousValue: prevVal,
    newValue: newVal,
    appliedAt: (h.timestamp ?? h.appliedAt ?? '') as string,
    appliedBy: (h.appliedBy ?? '') as string,
    reverted: (h.reverted ?? false) as boolean,
    revertedAt: (h.reverted_at ?? h.revertedAt) as string | undefined,
    status: (h.status ?? '') as string,
    device_mac: (h.device_mac ?? '') as string,
  };
}
