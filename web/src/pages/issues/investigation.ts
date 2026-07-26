/**
 * Page-local API for the LLM investigator (issue detail, §10).
 *
 * These endpoints landed after the shared `src/pages/shared/api.ts` layer, and
 * this feature is scoped to `pages/issues/**`, so its fetchers live here beside
 * the panel that uses them rather than being threaded back through shared. It
 * reuses the shared `ApiError` so callers get one consistent error type.
 */

import { ApiError } from '../shared/api';
import { authHeaders, clearToken, getToken, promptForToken } from '../../api/token';

export interface ProviderInfo {
  name: string;
  available: boolean;
  detail: string;
}

export interface InvestigationRow {
  id: number;
  issue_id: number;
  ts: number;
  provider: string;
  dossier_md: string;
  response_md: string | null;
  status: string; // pending | answered
}

const BASE = import.meta.env.VITE_API_URL ?? '';

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

export const listInvestigationProviders = () =>
  request<{ providers: ProviderInfo[] }>('/api/issues/investigate/providers').then(
    (r) => r.providers,
  );

export const listInvestigations = (issueId: number) =>
  request<{ investigations: InvestigationRow[]; count: number }>(
    `/api/issues/${issueId}/investigations`,
  ).then((r) => r.investigations);

export const startInvestigation = (issueId: number, provider: string) =>
  request<{ investigation: InvestigationRow }>(`/api/issues/${issueId}/investigate`, {
    method: 'POST',
    body: JSON.stringify({ provider }),
  }).then((r) => r.investigation);

export const importInvestigation = (issueId: number, text: string) =>
  request<{ investigation: InvestigationRow }>(`/api/issues/${issueId}/investigations/import`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  }).then((r) => r.investigation);
