import { useCallback, useEffect, useRef, useState } from 'react';
import { getHealth, listIssues } from './client';
import { ApiError } from './client';
import { isSuppressedNow } from '../pages/shared/format';
import type { Health, Issue } from './types';

/**
 * Small data hooks the shell needs. Page-level data layers are built by the
 * page agents; this file stays to what shared infrastructure uses (sidebar
 * counts, health) plus a generic async primitive to build on.
 */

export interface AsyncState<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Run an async fetch, tracking loading/error, safe against unmount. */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: readonly unknown[] = [],
): AsyncState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    setLoading(true);
    fn()
      .then((d) => {
        if (!mounted.current) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!mounted.current) return;
        setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (mounted.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

/** Poll the health endpoint. `intervalMs` of 0 disables polling. */
export function useHealth(intervalMs = 30_000): AsyncState<Health> {
  const state = useAsync<Health>(getHealth, []);
  const reload = state.reload;
  useEffect(() => {
    if (!intervalMs) return;
    const t = setInterval(reload, intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, reload]);
  return state;
}

export interface IssueSummary {
  open: number;
  active: number;
  p1: number;
  p2: number;
  p3: number;
  hasP1: boolean;
  /** Open issues an operator has suppressed (Gitea #49): excluded from `open`,
   * the per-severity counts, and `hasP1`, and disclosed alongside the badge so
   * the shrunk count is never silent. */
  suppressed: number;
}

function summarize(issues: Issue[], nowSec: number): IssueSummary {
  let p1 = 0;
  let p2 = 0;
  let p3 = 0;
  let active = 0;
  let suppressed = 0;
  for (const i of issues) {
    if (i.state === 'resolved') continue;
    // Suppressed issues leave the attention counts and the P1 badge alarm — the
    // operator's explicit call — but are disclosed as `suppressed` (Gitea #49).
    if (isSuppressedNow(i, nowSec)) {
      suppressed += 1;
      continue;
    }
    if (i.state === 'active') active += 1;
    if (i.severity === 'p1') p1 += 1;
    else if (i.severity === 'p2') p2 += 1;
    else p3 += 1;
  }
  return { open: p1 + p2 + p3, active, p1, p2, p3, hasP1: p1 > 0, suppressed };
}

/**
 * Open-issue counts for the sidebar badges (docs §Interaction: quiet gray
 * badges, red only when P1s exist). Polls, and can be nudged by a WS transition.
 */
export function useIssueSummary(intervalMs = 30_000) {
  const state = useAsync(() => listIssues(), []);
  const reload = state.reload;
  // Tracked in state, not read from `Date.now()` during render, so the badge is a
  // pure function of props+state. Refreshed on the same cadence as the poll, so a
  // timed suppression's expiry (Gitea #49) is re-derived when the list refetches.
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    if (!intervalMs) return;
    const t = setInterval(() => {
      setNowSec(Math.floor(Date.now() / 1000));
      reload();
    }, intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, reload]);

  const summary = state.data ? summarize(state.data.issues, nowSec) : undefined;
  return { ...state, summary };
}
