/**
 * First-run setup API client (docs/ARCHITECTURE.md §18).
 *
 * These three calls drive the onboarding flow *before* a bearer token exists, so
 * they deliberately do NOT go through the authed `request()` wrapper (which would
 * trip the global 401 handler). They are their own small, typed surface:
 *
 *   - `getSetupStatus()`   — the state-machine query the app branches on.
 *   - `detectConsole()`    — read-only console fingerprint + per-console playbook.
 *   - `connectController()`— writes credentials (server-side) and mints the token.
 *
 * The UniFi API key travels one way only (browser → daemon → `data/secrets.env`);
 * it is never returned. The UI access token is returned exactly once, by design.
 * Failures surface as a typed `SetupError` whose `kind` the UI maps to honest,
 * never-raw copy.
 */

import { authHeaders } from './token';

const BASE = import.meta.env.VITE_API_URL ?? '';

/* ---- Contract types (source of truth: netadmin/server/routers/setup.py) ---- */

/** `GET /api/setup/status`. `configured` is false only on a truly fresh install
 *  (no controller credential and no UI token) — the one state that unlocks setup. */
export interface SetupStatus {
  configured: boolean;
  controller_connected: boolean;
}

export type SetupAuthMode = 'api_key' | 'unifi_os_cookie' | 'legacy_cookie' | 'none';

/** Mirrors `ConsoleInfo.as_dict()` from netadmin/ingest/unifi/detect.py. */
export interface SetupConsoleInfo {
  kind: string;
  model: string | null;
  is_unifi_os: boolean;
  network_version: string | null;
  api_key_supported: boolean;
  api_key_status: string;
  recommended_auth: string;
  reachable: boolean;
  detail: string | null;
}

/** The resolved, ordered "how to get a credential" steps for this console kind. */
export interface SetupPlaybook {
  label: string;
  auth_mode: string;
  supports_api_key?: boolean;
  steps: string[];
}

/** `POST /api/setup/detect` → the fingerprint, the playbook, and where to open.
 *  The daemon does not echo the host (it's what the caller sent) — the flow uses
 *  the entered value and `console_url` for the "open my controller" link. */
export interface SetupDetectResponse {
  console_url: string;
  console: SetupConsoleInfo;
  playbook: SetupPlaybook;
}

/** `POST /api/setup/connect` → the UI token, shown exactly once. */
export interface SetupConnectResponse {
  ok: boolean;
  ui_token: string;
}

/** One console the LAN scan confirmed (source: netadmin/server/services/discovery.py).
 *  `host` is a ready-to-use address for the field (legacy consoles keep `:8443`). */
export interface SetupScanCandidate {
  host: string;
  port: number;
  kind: string;
  label: string;
  model: string | null;
  api_key_status: string;
}

/** `POST /api/setup/scan` → the /24(s) swept and the confirmed consoles. An empty
 *  `candidates` is the honest "none found — enter it manually" signal. */
export interface SetupScanResponse {
  ok: boolean;
  scanned: string[];
  candidates: SetupScanCandidate[];
}

export type SetupConnectBody =
  { host: string; api_key: string } | { host: string; username: string; password: string };

/* ---- Errors -------------------------------------------------------------- */

export type SetupErrorKind =
  /** Controller refused the credential (wrong key / bad login). */
  | 'rejected'
  /** Controller could not be reached from the daemon. */
  | 'unreachable'
  /** Setup already completed — the endpoint has locked itself (409). */
  | 'conflict'
  /** The daemon itself could not be reached from the browser. */
  | 'network'
  /** Unexpected daemon-side error. */
  | 'server';

export class SetupError extends Error {
  readonly kind: SetupErrorKind;
  readonly status: number;
  /** A clean, short server-provided message when present; never a raw trace. */
  readonly detail?: string;

  constructor(kind: SetupErrorKind, message: string, status = 0, detail?: string) {
    super(message);
    this.name = 'SetupError';
    this.kind = kind;
    this.status = status;
    this.detail = detail;
  }
}

/** Pull the clean message off an error response — the setup router sends
 *  `{ok:false, code, error}`, so prefer `error`, then FastAPI's `detail`. Only a
 *  short, clean string is used; never echo a raw body or a stack trace to the UI. */
async function readDetail(res: Response): Promise<string | undefined> {
  try {
    const data: unknown = await res.json();
    if (data && typeof data === 'object') {
      const rec = data as Record<string, unknown>;
      for (const key of ['error', 'detail'] as const) {
        const v = rec[key];
        if (typeof v === 'string' && v.trim() && v.trim().length <= 240) {
          return v.trim();
        }
      }
    }
  } catch {
    /* body was not JSON — fall through to category copy */
  }
  return undefined;
}

/* ---- Calls --------------------------------------------------------------- */

/**
 * Read the setup state machine. Public while unconfigured. If a configured
 * install has auth-gated this endpoint, a 401/403 is itself the answer
 * (`configured: true`) — route such a browser to the token gate, not the flow.
 */
export async function getSetupStatus(): Promise<SetupStatus> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/setup/status`, {
      headers: { Accept: 'application/json', ...authHeaders() },
    });
  } catch (cause) {
    throw new SetupError('network', `network error: ${(cause as Error).message}`);
  }
  if (res.status === 401 || res.status === 403) {
    return { configured: true, controller_connected: false };
  }
  if (!res.ok) {
    throw new SetupError(
      res.status >= 500 ? 'server' : 'network',
      `${res.status} ${res.statusText}`.trim(),
      res.status,
    );
  }
  return (await res.json()) as SetupStatus;
}

/**
 * Scan the daemon host's own private LAN for a reachable UniFi console (read-only:
 * TCP probes + the login-free fingerprint). Pre-auth only while unconfigured, like
 * `detect`. A scan that finds nothing resolves with an empty `candidates` list —
 * the honest "enter it manually" path — rather than throwing.
 */
export async function scanForConsoles(): Promise<SetupScanResponse> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/setup/scan`, {
      method: 'POST',
      headers: { Accept: 'application/json' },
    });
  } catch (cause) {
    throw new SetupError('network', `network error: ${(cause as Error).message}`);
  }
  if (res.status === 409) {
    throw new SetupError('conflict', 'setup already completed', 409, await readDetail(res));
  }
  if (!res.ok) {
    const detail = await readDetail(res);
    throw new SetupError(
      res.status >= 500 ? 'server' : 'rejected',
      `${res.status} ${res.statusText}`.trim(),
      res.status,
      detail,
    );
  }
  return (await res.json()) as SetupScanResponse;
}

/** Fingerprint the console at `host` (read-only). Never mutates the controller. */
export async function detectConsole(host: string): Promise<SetupDetectResponse> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/setup/detect`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ host }),
    });
  } catch (cause) {
    throw new SetupError('network', `network error: ${(cause as Error).message}`);
  }
  if (res.status === 409) {
    throw new SetupError('conflict', 'setup already completed', 409, await readDetail(res));
  }
  if (!res.ok) {
    const detail = await readDetail(res);
    throw new SetupError(
      res.status >= 500 ? 'server' : 'rejected',
      `${res.status} ${res.statusText}`.trim(),
      res.status,
      detail,
    );
  }
  return (await res.json()) as SetupDetectResponse;
}

/**
 * Validate the credential (read-only probe, server-side), persist it to
 * `data/secrets.env`, and mint the UI token. Returns the token exactly once.
 * The endpoint 409s if the install is already configured — surfaced as `conflict`.
 */
export async function connectController(body: SetupConnectBody): Promise<SetupConnectResponse> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api/setup/connect`, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
  } catch (cause) {
    throw new SetupError('network', `network error: ${(cause as Error).message}`);
  }
  if (!res.ok) {
    const detail = await readDetail(res);
    const kind: SetupErrorKind =
      res.status === 409
        ? 'conflict'
        : res.status === 502 || res.status === 503 || res.status === 504
          ? 'unreachable'
          : res.status >= 500
            ? 'server'
            : 'rejected';
    throw new SetupError(kind, `${res.status} ${res.statusText}`.trim(), res.status, detail);
  }
  return (await res.json()) as SetupConnectResponse;
}
