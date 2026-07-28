import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getUpdateStatus,
  isUpgradeInProgress,
  UpdateApiError,
  type UpdateStatus,
} from '../../api/update';

/**
 * Polls `GET /api/system/update` for the update banner (docs/ARCHITECTURE.md §23).
 *
 * Two cadences: a slow 60s poll normally (there is rarely anything new — the
 * daemon only re-checks PyPI on `updates.interval_s`, six hours by default), and
 * a fast 4s poll while a self-upgrade is actively running, so the banner's
 * progress line tracks the runner's phase closely. Settings → Software update
 * forces a re-check out of band, and this poll picks that result up within 60s.
 *
 * The runner restarts the daemon partway through (`swapping` → `restarting` →
 * `verifying`), so the poll is *expected* to fail for a few seconds around the
 * restart — that is not a real error, it's the daemon coming back up. While an
 * upgrade is in progress, a failed poll is swallowed and the last known state is
 * kept on screen rather than replaced with an error card; once the phase leaves
 * the in-progress set (or on the very first load) a real failure surfaces normally.
 */

interface UpdateStatusState {
  status: UpdateStatus | undefined;
  error: UpdateApiError | null;
  loading: boolean;
  /** Reload from the server (used after dismiss/apply, and by a manual retry). */
  reload: () => void;
  /** Replace the held status directly with a response body a mutation already
   * returned, so the UI reflects it instantly instead of waiting on the next poll. */
  setStatus: (status: UpdateStatus) => void;
}

const SLOW_POLL_MS = 60_000;
const FAST_POLL_MS = 4_000;

export function useUpdateStatus(): UpdateStatusState {
  const [status, setStatusState] = useState<UpdateStatus>();
  const [error, setError] = useState<UpdateApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const statusRef = useRef<UpdateStatus | undefined>(undefined);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await getUpdateStatus();
      if (!mounted.current) return;
      statusRef.current = next;
      setStatusState(next);
      setError(null);
    } catch (e) {
      if (!mounted.current) return;
      const wasActive = isUpgradeInProgress(statusRef.current?.upgrade_state?.phase);
      if (wasActive) return; // transient — the daemon is mid-restart, keep the last state
      setError(e instanceof UpdateApiError ? e : new UpdateApiError(0, String(e)));
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const active = isUpgradeInProgress(status?.upgrade_state?.phase);
  useEffect(() => {
    const t = setInterval(load, active ? FAST_POLL_MS : SLOW_POLL_MS);
    return () => clearInterval(t);
  }, [active, load]);

  const setStatus = useCallback((next: UpdateStatus) => {
    statusRef.current = next;
    setStatusState(next);
    setError(null);
  }, []);

  return { status, error, loading, reload: load, setStatus };
}
