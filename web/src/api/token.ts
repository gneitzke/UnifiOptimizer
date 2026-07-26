/**
 * API token store + just-in-time prompt (docs/ARCHITECTURE.md §12 + §18.1).
 *
 * Under the §18.1 model, GET reads are open once the daemon is configured; only
 * state-changing calls (fix apply/revert, ack/snooze, setup/connect, token
 * regenerate) need the token, plus the `/ws` socket. This module is the single
 * source of truth for it: persisted in localStorage, attached by every fetch
 * wrapper and the WebSocket URL, and cleared by "sign out".
 *
 * It also owns the just-in-time prompt. There is NO returning-user wall — viewing
 * never blocks. When a mutating call finds no valid token, it calls
 * `promptForToken()`, which opens the small modal (`TokenPrompt`) and resolves with
 * the entered token so the original action can proceed. A configured install with
 * no stored token still loads the dashboard; the first fix you apply is what asks.
 */

const STORAGE_KEY = 'netadmin_api_token';

type Listener = () => void;
const listeners = new Set<Listener>();

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

let cached: string | null = readStored();

// Just-in-time prompt state. `promptOpen` drives the modal; `pendingResolvers`
// are the awaiting mutating calls that resume once a token is entered (or reject
// when the operator dismisses the modal).
let promptOpen = false;
let promptMessage: string | null = null;
let pendingResolvers: Array<(token: string | null) => void> = [];

function emit(): void {
  for (const l of Array.from(listeners)) {
    try {
      l();
    } catch {
      /* one bad subscriber must not starve the others */
    }
  }
}

/** Subscribe to token / prompt-state changes. Returns an unsubscribe. */
export function subscribeAuth(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getToken(): string | null {
  return cached;
}

export function setToken(token: string): void {
  cached = token.trim() || null;
  try {
    if (cached) localStorage.setItem(STORAGE_KEY, cached);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage may be unavailable; the in-memory value still applies this session */
  }
  emit();
}

/** Forget the token ("sign out" / a rejected token). Never raises a wall — the
 *  next mutating action simply re-prompts just-in-time. */
export function clearToken(): void {
  cached = null;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  emit();
}

/** `{ Authorization: 'Bearer <token>' }` when a token is set, else `{}`. */
export function authHeaders(): Record<string, string> {
  return cached ? { Authorization: `Bearer ${cached}` } : {};
}

/** `?token=<token>` for the WebSocket URL (browsers cannot set WS headers), else ''. */
export function wsTokenQuery(): string {
  return cached ? `?token=${encodeURIComponent(cached)}` : '';
}

/* ---- Just-in-time prompt ------------------------------------------------- */

export function isTokenPromptOpen(): boolean {
  return promptOpen;
}

export function tokenPromptMessage(): string | null {
  return promptMessage;
}

/**
 * Open the just-in-time access-token modal and resolve once the operator responds.
 *
 * Resolves with the entered token (also stored) on submit, or `null` when the
 * modal is dismissed — the caller then surfaces the original 401 rather than
 * retrying. Concurrent mutating calls share the one modal: all waiters resolve
 * together, so each resumes with the freshly-entered token.
 */
export function promptForToken(message?: string): Promise<string | null> {
  promptMessage = message ?? null;
  promptOpen = true;
  emit();
  return new Promise((resolve) => {
    pendingResolvers.push(resolve);
  });
}

function settlePrompt(token: string | null): void {
  promptOpen = false;
  promptMessage = null;
  const resolvers = pendingResolvers;
  pendingResolvers = [];
  if (token) {
    setToken(token); // emits (token change → WS reconnect + modal close)
  } else {
    emit(); // still notify subscribers to render the closed modal
  }
  for (const resolve of resolvers) {
    try {
      resolve(token);
    } catch {
      /* one bad waiter must not starve the others */
    }
  }
}

/** Modal "save": store the token and resume every awaiting mutating call. */
export function submitTokenPrompt(token: string): void {
  settlePrompt(token.trim() || null);
}

/** Modal "cancel"/dismiss: resolve waiters with null so callers surface the 401. */
export function cancelTokenPrompt(): void {
  settlePrompt(null);
}

/** Reactive fallback: a request path that unexpectedly 401'd raises the prompt
 *  without a full-screen wall. Mutating wrappers use `promptForToken()` (which
 *  awaits + retries); this is the fire-and-forget path for read wrappers. */
export function markAuthRequired(): void {
  if (!promptOpen) {
    promptOpen = true;
    promptMessage = null;
    emit();
  }
}
