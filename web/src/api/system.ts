/**
 * Access-token API client (docs/ARCHITECTURE.md §18.1 Settings addendum).
 *
 * Two calls back the Settings "Access token" section — how a user finds or rotates
 * the token a just-in-time fix prompt asks for:
 *
 *   - `revealSystemToken()`     — `GET /api/system/token`. The daemon gates this
 *     behind the bearer token OR a loopback peer (on-box recovery), so from another
 *     device it 401s without a token; the guard then prompts just-in-time.
 *   - `regenerateSystemToken()` — `POST /api/system/token/regenerate`. Mutating +
 *     token-gated; mints a new token and returns it once.
 *
 * Both go through a reactive guard: fire, and on a 401 drop the stale token, prompt
 * for the access token over the live view, and retry. On loopback the reveal simply
 * succeeds with no token and no prompt.
 */

import { authHeaders, clearToken, getToken, promptForToken } from './token';

const BASE = import.meta.env.VITE_API_URL ?? '';

export interface SystemTokenInfo {
  /** The current access token, or null on an unconfigured / open install. */
  token: string | null;
  configured: boolean;
}

export class SystemTokenError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'SystemTokenError';
    this.status = status;
  }
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
    throw new SystemTokenError(0, `network error: ${(cause as Error).message}`);
  }
  if (!res.ok) {
    throw new SystemTokenError(res.status, `${res.status} ${res.statusText}`.trim());
  }
  return (await res.json()) as T;
}

async function guarded<T>(path: string, init?: RequestInit): Promise<T> {
  let firstTry = true;
  for (;;) {
    try {
      return await send<T>(path, init);
    } catch (e) {
      if (!(e instanceof SystemTokenError) || e.status !== 401) throw e;
      if (getToken()) clearToken();
      const entered = await promptForToken(
        firstTry ? undefined : 'That token was not accepted. Try again.',
      );
      firstTry = false;
      if (!entered) throw e;
    }
  }
}

/** Reveal the current access token (loopback-open; token-gated from elsewhere). */
export const revealSystemToken = () => guarded<SystemTokenInfo>('/api/system/token');

/** Mint a new access token, returned once. The old token stops working. */
export const regenerateSystemToken = () =>
  guarded<{ token: string }>('/api/system/token/regenerate', { method: 'POST' });
