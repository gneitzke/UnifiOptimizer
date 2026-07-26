import { ApiError, listEvents, useAsync, type NetEvent } from '../../api';
import type { WindowSpec } from './buckets';

/**
 * Fetch the events for one window on top of the shared `useAsync` primitive. We
 * pull a single capped page (the endpoint is newest-first, hard-capped) and
 * bucket client-side — the density view needs the full set, not a downsample.
 *
 * `nowTs` is captured *inside* the fetch, so the window bounds and the "as of"
 * stamp agree and never drift between renders (and the anchor rides along with
 * the data instead of a second setState).
 */

export interface TimelineData {
  events: NetEvent[];
  nowTs: number;
  fetchedAt: number;
  loading: boolean;
  error: ApiError | null;
  /** True when the cap was hit — the window may extend past the oldest event shown. */
  capped: boolean;
  reload: () => void;
}

const CAP = 1000;

interface Fetched {
  events: NetEvent[];
  nowTs: number;
}

export function useTimelineEvents(spec: WindowSpec): TimelineData {
  const { data, error, loading, reload } = useAsync<Fetched>(async () => {
    const now = Math.floor(Date.now() / 1000);
    const res = await listEvents({ since_ts: now - spec.seconds, limit: CAP });
    return { events: res.events, nowTs: now };
  }, [spec.seconds]);

  const events = data?.events ?? [];
  // 0 until the first fetch resolves; the chart is gated on events.length, so
  // this anchor is never consumed before it is real (and stays render-pure).
  const nowTs = data?.nowTs ?? 0;

  return {
    events,
    nowTs,
    fetchedAt: nowTs,
    loading,
    error,
    capped: events.length >= CAP,
    reload,
  };
}
