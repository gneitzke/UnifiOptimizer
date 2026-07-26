import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api';

/**
 * Async data hook for the page surfaces — mirrors `src/api/hooks::useAsync` but
 * is aware of this layer's `ApiError` (so a 404 keeps its status, which the
 * metric-chart empty state depends on) and supports optional polling.
 *
 * INTEGRATE NOTE: converges with `src/api/hooks::useAsync` at the integrate pass.
 */
export interface PageAsyncState<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

export function usePageAsync<T>(
  fn: () => Promise<T>,
  deps: readonly unknown[] = [],
  opts: { pollMs?: number } = {},
): PageAsyncState<T> {
  const { pollMs } = opts;
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
    let active = true;
    setLoading(true);
    fn()
      .then((d) => {
        if (!active || !mounted.current) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!active || !mounted.current) return;
        setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
      })
      .finally(() => {
        if (active && mounted.current) setLoading(false);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!pollMs) return;
    const t = setInterval(reload, pollMs);
    return () => clearInterval(t);
  }, [pollMs, reload]);

  return { data, error, loading, reload };
}

/**
 * Current time in epoch seconds, refreshed on an interval so relative durations
 * ("ongoing 6d") stay live. Reading the clock in a state initializer keeps render
 * pure (the `Date.now()` call is not in the render body).
 */
export function useNowSeconds(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000));
  useEffect(() => {
    const t = setInterval(() => setNow(Math.floor(Date.now() / 1000)), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
