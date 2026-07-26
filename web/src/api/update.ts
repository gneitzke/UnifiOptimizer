/**
 * Self-update API client (docs/ARCHITECTURE.md §23).
 *
 * Backs the dashboard update banner. `GET /api/system/update` is an open read
 * (no token, even on a configured install — same posture as `/api/health`);
 * the three mutations (`dismiss`, `check`, `apply`) go through the same
 * just-in-time token guard as `revealSystemToken`/`regenerateSystemToken` in
 * `./system.ts` — on a 401 they raise the shared `TokenPrompt` and retry once
 * a token is entered, rather than failing the click outright.
 *
 * `apply` additionally fails closed (403) on an install with no token
 * configured at all, and 409s on a stale target version, an unsupported
 * install, or an upgrade already in flight (`netadmin/server/routers/system.py`).
 * Those are real, expected outcomes the banner shows inline — not retried like
 * a 401 — so `send`/`guarded` here carry the server's `detail` string through
 * on the error, unlike the terser `SystemTokenError`.
 */

import { authHeaders, clearToken, getToken, promptForToken } from './token';

const BASE = import.meta.env.VITE_API_URL ?? '';

export type InstallMethod = 'pip' | 'container' | 'addon' | 'source';
export type UpdateVariant = 'compose' | 'macmini' | null;

/** The runner's state machine (`netadmin/upgrade/journal.py`), in order. */
export type UpgradePhase =
  | 'starting'
  | 'preflight'
  | 'downloading'
  | 'staging'
  | 'smoke_testing'
  | 'backing_up'
  | 'swapping'
  | 'restarting'
  | 'verifying'
  | 'done'
  | 'rolled_back'
  | 'failed';

/** Phases the runner is still actively working through — mirrors the
 * backend's `IN_PROGRESS_PHASES` (never re-derive this from the terminal
 * list; keep both named explicitly so a new phase can't silently join the
 * wrong side). */
export const UPGRADE_IN_PROGRESS_PHASES: ReadonlySet<UpgradePhase> = new Set([
  'starting',
  'preflight',
  'downloading',
  'staging',
  'smoke_testing',
  'backing_up',
  'swapping',
  'restarting',
  'verifying',
]);

export function isUpgradeInProgress(phase: UpgradePhase | undefined | null): boolean {
  return !!phase && UPGRADE_IN_PROGRESS_PHASES.has(phase);
}

/** The journal's public projection — never the recorded pid/argv/cwd/env. */
export interface UpgradeState {
  phase: UpgradePhase;
  target_version: string;
  from_version: string;
  started_ts: number;
  updated_ts: number;
  error: string | null;
}

export interface UpdateStatus {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  install_method: InstallMethod;
  variant: UpdateVariant;
  self_upgrade_supported: boolean;
  checked_ts: number | null;
  skipped_version: string | null;
  snoozed_until: number | null;
  upgrade_state: UpgradeState | null;
  release_url: string | null;
}

export type DismissMode = 'skip' | 'snooze';

export class UpdateApiError extends Error {
  readonly status: number;
  /** A clean, short server-provided message when present (e.g. a 409's
   * `detail`) — shown to the operator instead of a bare status code. */
  readonly detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = 'UpdateApiError';
    this.status = status;
    this.detail = detail;
  }
}

async function readDetail(res: Response): Promise<string | undefined> {
  try {
    const data: unknown = await res.clone().json();
    if (data && typeof data === 'object') {
      const v = (data as Record<string, unknown>).detail;
      if (typeof v === 'string' && v.trim()) return v.trim();
    }
  } catch {
    /* not JSON — no detail to surface */
  }
  return undefined;
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
    throw new UpdateApiError(0, `network error: ${(cause as Error).message}`);
  }
  if (!res.ok) {
    const detail = await readDetail(res);
    throw new UpdateApiError(res.status, `${res.status} ${res.statusText}`.trim(), detail);
  }
  return (await res.json()) as T;
}

/** Same just-in-time retry loop as `system.ts`'s `guarded()`: a 401 raises the
 * shared token prompt and retries once, anything else (403 fail-closed, 409
 * conflict) is rethrown for the caller to show inline. */
async function guarded<T>(path: string, init?: RequestInit): Promise<T> {
  let firstTry = true;
  for (;;) {
    try {
      return await send<T>(path, init);
    } catch (e) {
      if (!(e instanceof UpdateApiError) || e.status !== 401) throw e;
      if (getToken()) clearToken();
      const entered = await promptForToken(
        firstTry ? undefined : 'That token was not accepted. Try again.',
      );
      firstTry = false;
      if (!entered) throw e;
    }
  }
}

/** `GET /api/system/update` — open read, no token needed even when configured. */
export const getUpdateStatus = () => send<UpdateStatus>('/api/system/update');

/** `POST /api/system/update/dismiss` — server-side skip/snooze, survives across
 * browsers and devices (never `localStorage`). */
export const dismissUpdate = (mode: DismissMode) =>
  guarded<UpdateStatus>('/api/system/update/dismiss', {
    method: 'POST',
    body: JSON.stringify({ mode }),
  });

/** `POST /api/system/update/check` — force a PyPI re-check right now. */
export const forceCheckUpdate = () =>
  guarded<UpdateStatus>('/api/system/update/check', { method: 'POST' });

/** `POST /api/system/update/apply` — kick off the pip self-upgrade runner.
 * `target_version` must equal the currently advertised latest (kills a stale-tab
 * race); the caller reads `UpdateApiError.detail` on a 409 to show why not. */
export const applyUpdate = (targetVersion: string) =>
  guarded<UpdateStatus>('/api/system/update/apply', {
    method: 'POST',
    body: JSON.stringify({ target_version: targetVersion }),
  });
