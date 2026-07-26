import { useMemo } from 'react';
import { TimeSeriesChart } from '../../components/ui/TimeSeriesChart';
import { Skeleton } from '../../components/ui/Skeleton';
import type { ChartPoint } from '../../components/ui/chart-utils';
import { fmt } from '../../components/ui/chart-utils';
import { getMetricWindow, type MetricBucket } from '../shared/api';
import { usePageAsync } from '../shared/hooks';
import { metricMeta } from '../devices/metrics';
import type { MetricHint } from './metricHints';

/**
 * The issue's related-metric chart(s): the series the detector actually watched,
 * pulled from `/api/metrics/window` for the issue's entity. A 404 (the entity
 * never reported that metric) is rendered as an honest "no series" note, never a
 * flat fake line. Data gaps stay gaps (null breakpoints where buckets are
 * missing) — the SVG draws the discontinuity.
 */

const WINDOW_S = 86_400;
const POINTS = 240;

/** Median positive spacing of the returned buckets = the series' native cadence. */
function nativeCadence(buckets: MetricBucket[], fallback: number): number {
  const deltas: number[] = [];
  for (let i = 1; i < buckets.length; i++) {
    const d = buckets[i].ts - buckets[i - 1].ts;
    if (d > 0) deltas.push(d);
  }
  if (deltas.length === 0) return Math.max(1, fallback);
  deltas.sort((a, b) => a - b);
  return Math.max(1, deltas[Math.floor(deltas.length / 2)]);
}

function bucketsToPoints(buckets: MetricBucket[], startTs: number, endTs: number): ChartPoint[] {
  if (buckets.length === 0) return [];
  const cadence = nativeCadence(buckets, (endTs - startTs) / Math.max(1, buckets.length));
  const threshold = cadence * 1.5;
  const out: ChartPoint[] = [];
  let prev: number | null = null;
  for (const b of buckets) {
    if (prev != null && b.ts - prev > threshold) {
      out.push({ ts: prev + cadence, value: null });
    }
    out.push({ ts: b.ts, value: b.avg, min: b.min, max: b.max });
    prev = b.ts;
  }
  return out;
}

function SingleMetricChart({ hint }: { hint: MetricHint }) {
  const { data, loading, error } = usePageAsync(
    () =>
      getMetricWindow({
        entity_id: hint.entityId,
        metric: hint.metric,
        seconds: WINDOW_S,
        points: POINTS,
      }),
    [hint.entityId, hint.metric],
  );

  const { points, summary, takeaway, asOf, stale } = useMemo(() => {
    if (!data)
      return {
        points: [] as ChartPoint[],
        summary: undefined,
        takeaway: undefined,
        asOf: null as number | null,
        stale: false,
      };
    const pts = bucketsToPoints(data.buckets, data.start_ts, data.end_ts);
    // Range from the bucket ENVELOPE (min of mins, max of maxes), not the averages
    // — an intra-bucket trough/spike must be reported, not smoothed away.
    const los = data.buckets.map((b) => b.min).filter((v): v is number => v != null);
    const his = data.buckets.map((b) => b.max).filter((v): v is number => v != null);
    const lastBucket = [...data.buckets].reverse().find((b) => b.avg != null) ?? null;
    const last = lastBucket?.avg ?? null;
    const unit = hint.unit ? ` ${hint.unit}` : '';
    const summaryStat =
      last != null ? `${fmt(last, Math.abs(last) < 10 ? 1 : 0)}${unit} latest` : undefined;
    const takeawayText =
      los.length > 0 && his.length > 0
        ? `min ${fmt(Math.min(...los), 1)} · max ${fmt(Math.max(...his), 1)}${unit} over 24h`
        : undefined;
    const cadence = nativeCadence(data.buckets, (data.end_ts - data.start_ts) / POINTS);
    const isStale = lastBucket != null ? data.end_ts - lastBucket.ts > cadence * 2.5 : false;
    return {
      points: pts,
      summary: summaryStat,
      takeaway: takeawayText,
      asOf: lastBucket?.ts ?? null,
      stale: isStale,
    };
  }, [data, hint.unit]);

  const meta = metricMeta(hint.metric);

  if (error && error.status === 404) {
    return (
      <div className="t-caption py-2" style={{ color: 'var(--fg-subtle)' }}>
        No {hint.label.toLowerCase()} series was recorded for this entity.
      </div>
    );
  }
  if (error) {
    return (
      <div className="t-caption py-2" style={{ color: 'var(--fg-muted)' }}>
        Could not load {hint.label.toLowerCase()}.
      </div>
    );
  }
  if (loading && !data) {
    return <Skeleton className="h-[180px] w-full" />;
  }

  return (
    <TimeSeriesChart
      series={[{ name: hint.label, points, fill: true }]}
      height={180}
      percentage={hint.percentage}
      zeroBaseline={hint.unit !== 'dBm'}
      domain={meta.domain}
      reference={meta.reference}
      yUnit={hint.unit}
      contextLabel={hint.label}
      summaryStat={summary}
      asOf={asOf}
      stale={stale}
      takeaway={takeaway}
    />
  );
}

export function MetricEvidenceChart({ hints }: { hints: MetricHint[] }) {
  if (hints.length === 0) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
        No metric series is linked to this evidence.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-5">
      {hints.map((h) => (
        <SingleMetricChart key={`${h.entityId}-${h.metric}`} hint={h} />
      ))}
    </div>
  );
}
