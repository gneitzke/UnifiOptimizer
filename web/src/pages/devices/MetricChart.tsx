import { useAsync } from '../../api';
import {
  TimeSeriesChart,
  Skeleton,
  Card,
  fmt,
  type ChartPoint,
  type Series,
} from '../../components/ui';
import { getMetricWindow, type WindowBucket } from './api';
import { metricMeta } from './metrics';

/**
 * One hand-rolled metric chart backed by /api/metrics/window (docs §Charts).
 *
 * The endpoint already downsamples server-side and OMITS empty buckets, so a
 * data hole arrives as a time jump between consecutive buckets. To keep the
 * design contract's "render gaps as gaps, never interpolate" (never-do rule 8),
 * we re-insert an explicit null wherever two buckets are more than ~1.5× the
 * series' OWN native cadence apart (the median spacing of the returned buckets,
 * not seconds/points) — so any true hole breaks the line even on a wide window.
 * Each point also carries the bucket's min/max envelope so an intra-bucket
 * trough/spike shows as a faint spread band instead of being averaged away.
 */

interface Props {
  entityId: number;
  metric: string;
  seconds: number;
  /** How many buckets the window was asked for; drives gap detection. */
  points?: number;
  height?: number;
}

/** Median positive spacing of the returned buckets = the series' native cadence. */
function nativeCadence(buckets: WindowBucket[], fallback: number): number {
  const deltas: number[] = [];
  for (let i = 1; i < buckets.length; i++) {
    const d = buckets[i].ts - buckets[i - 1].ts;
    if (d > 0) deltas.push(d);
  }
  if (deltas.length === 0) return Math.max(1, fallback);
  deltas.sort((a, b) => a - b);
  return Math.max(1, deltas[Math.floor(deltas.length / 2)]);
}

function toPoints(buckets: WindowBucket[], fallbackWidth: number): ChartPoint[] {
  const cadence = nativeCadence(buckets, fallbackWidth);
  const threshold = cadence * 1.5;
  const out: ChartPoint[] = [];
  let prevTs: number | null = null;
  for (const b of buckets) {
    if (prevTs != null && b.ts - prevTs > threshold) {
      // A real hole in the data — break the line so it is not interpolated.
      out.push({ ts: prevTs + cadence, value: null });
    }
    out.push({ ts: b.ts, value: b.avg, min: b.min, max: b.max });
    prevTs = b.ts;
  }
  return out;
}

export function MetricChart({ entityId, metric, seconds, points = 240, height = 200 }: Props) {
  const meta = metricMeta(metric);
  const { data, loading, error } = useAsync(
    () => getMetricWindow(entityId, metric, seconds, points),
    [entityId, metric, seconds, points],
  );

  if (loading && !data) {
    return (
      <Card pad="sm" className="flex flex-col gap-2">
        <Skeleton width="40%" height={14} />
        <Skeleton width="100%" height={height} />
      </Card>
    );
  }

  // A 404 means the entity never reported this metric — state it plainly rather
  // than drawing an empty axis (honest empty state, docs §Interaction).
  if (error && error.status === 404) {
    return (
      <Card pad="sm" className="flex flex-col gap-1">
        <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {meta.label}
        </div>
        <div className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
          Not reported for this entity.
        </div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card pad="sm" className="flex flex-col gap-1">
        <div className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {meta.label}
        </div>
        <div className="t-secondary" style={{ color: 'var(--sev-p2)' }}>
          Could not load ({error.status || 'network'}).
        </div>
      </Card>
    );
  }

  const buckets = data?.buckets ?? [];
  const expectedWidth = seconds / (data?.points || points);
  const chartPoints = toPoints(buckets, expectedWidth);

  const values = buckets.map((b) => b.avg).filter((v) => Number.isFinite(v));
  const lastBucket = buckets.length ? buckets[buckets.length - 1] : null;
  const latest = lastBucket ? lastBucket.avg : null;
  const lo = values.length ? Math.min(...buckets.map((b) => b.min)) : null;
  const hi = values.length ? Math.max(...buckets.map((b) => b.max)) : null;

  const unit = meta.unit ? ` ${meta.unit}` : '';
  // The value, plus an honest "as of HH:MM:SS" stamp (not a bare "now"): a line
  // that simply ends because ingestion stalled must not read as live.
  const summaryStat =
    latest != null ? `${fmt(latest, meta.digits)}${unit}` : undefined;
  // Stale = latest bucket lags the server's fetch clock by >2.5 cadences (judged
  // from the response's own end_ts, so it stays pure and clock-skew-free).
  const nativeGap = nativeCadence(buckets, expectedWidth);
  const isStale =
    lastBucket != null && data != null
      ? data.end_ts - lastBucket.ts > nativeGap * 2.5
      : false;
  const takeaway =
    lo != null && hi != null
      ? `Range ${fmt(lo, meta.digits)}–${fmt(hi, meta.digits)}${unit}` +
        (data && data.tier !== 'raw' ? ` · ${data.tier} rollup` : '')
      : undefined;

  const series: Series[] = [
    { name: meta.label, points: chartPoints, kind: 'line' },
  ];

  return (
    <Card pad="sm">
      <TimeSeriesChart
        series={series}
        height={height}
        percentage={meta.percentage}
        zeroBaseline={meta.unit !== 'dBm'}
        domain={meta.domain}
        reference={meta.reference}
        yUnit={meta.unit || undefined}
        contextLabel={meta.label}
        summaryStat={summaryStat}
        asOf={lastBucket ? lastBucket.ts : null}
        stale={isStale}
        takeaway={takeaway}
      />
    </Card>
  );
}
