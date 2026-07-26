import { useCallback, useEffect, useRef, useState } from 'react';
import { getHealth, listIssues } from './client';
import { ApiError } from './client';
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
}

function summarize(issues: Issue[]): IssueSummary {
  let p1 = 0;
  let p2 = 0;
  let p3 = 0;
  let active = 0;
  for (const i of issues) {
    if (i.state === 'resolved') continue;
    if (i.state === 'active') active += 1;
    if (i.severity === 'p1') p1 += 1;
    else if (i.severity === 'p2') p2 += 1;
    else p3 += 1;
  }
  return { open: p1 + p2 + p3, active, p1, p2, p3, hasP1: p1 > 0 };
}

/**
 * Open-issue counts for the sidebar badges (docs §Interaction: quiet gray
 * badges, red only when P1s exist). Polls, and can be nudged by a WS transition.
 */
export function useIssueSummary(intervalMs = 30_000) {
  const state = useAsync(() => listIssues(), []);
  const reload = state.reload;
  useEffect(() => {
    if (!intervalMs) return;
    const t = setInterval(reload, intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, reload]);

  const summary = state.data ? summarize(state.data.issues) : undefined;
  return { ...state, summary };
}
