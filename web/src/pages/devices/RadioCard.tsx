import { useAsync } from '../../api';
import { Card, Sparkline, Skeleton, fmt } from '../../components/ui';
import { getMetricWindow, type ChildEntity } from './api';
import { sampleMap, metricMeta } from './metrics';

/**
 * One radio on an AP (docs §Devices/:id: "per-radio cards — channel/width/power/
 * cu_total sparkline"). Shows the band + channel from state/meta and the current
 * airtime, clients, satisfaction, and retries, with a 24h channel-utilization
 * sparkline. Width and TX power are shown only when the controller reports them
 * (this dataset carries band + channel but not width/power) — no fabricated
 * fields (never-do rule 8).
 */

const BAND_LABEL: Record<string, string> = {
  ng: '2.4 GHz',
  na: '5 GHz',
  '6e': '6 GHz',
};

function bandLabel(radio: ChildEntity): string {
  const band = String((radio.meta as { band?: string }).band ?? '');
  return BAND_LABEL[band] ?? band.toUpperCase() ?? 'Radio';
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
        {label}
      </span>
      <span className="t-body tnum" style={{ color: 'var(--fg)' }}>
        {value}
      </span>
    </div>
  );
}

export function RadioCard({ radio }: { radio: ChildEntity }) {
  const m = sampleMap(radio.metrics);
  const val = (k: string) => m.get(k)?.value ?? null;

  const channel = radio.state?.channel ?? '—';
  const width = (radio.meta as { ht?: string | null }).ht;

  const { data } = useAsync(
    () => getMetricWindow(radio.entity_id, 'cu_total', 86_400, 48),
    [radio.entity_id],
  );
  const spark = data?.buckets.map((b) => b.avg) ?? null;

  const cu = val('cu_total');
  const cuMeta = metricMeta('cu_total');

  const stat = (k: string): string => {
    let v = val(k);
    const meta = metricMeta(k);
    // The controller reports −1 for satisfaction when it has no reading. That is
    // "unknown", not a catastrophic 0-ish score — render it as missing, never as a
    // real value (never-do rule 8: no sentinel shown as data).
    if (v != null && k === 'satisfaction' && v < 0) v = null;
    return v == null ? '—' : `${fmt(v, meta.digits)}${meta.unit ? ` ${meta.unit}` : ''}`;
  };

  return (
    <Card pad="sm" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <span className="t-label" style={{ color: 'var(--fg)' }}>
          {bandLabel(radio)}
        </span>
        <span className="t-caption tnum" style={{ color: 'var(--fg-muted)' }}>
          ch {channel}
          {width ? ` · ${width} MHz` : ''}
        </span>
      </div>

      {/* Channel utilization: current value + a 24h trend, never a bare number. */}
      <div className="flex items-end justify-between gap-3">
        <div className="flex flex-col">
          <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
            {cuMeta.label}
          </span>
          <span className="t-metric" style={{ color: 'var(--fg)' }}>
            {cu == null ? '—' : `${fmt(cu, 0)}`}
            <span className="t-secondary ml-0.5" style={{ color: 'var(--fg-subtle)' }}>
              %
            </span>
          </span>
        </div>
        {spark && spark.length > 1 ? (
          <Sparkline
            data={spark}
            percentage
            width={120}
            height={34}
            ariaLabel="Channel utilization, last 24 hours"
          />
        ) : data ? null : (
          <Skeleton width={120} height={34} />
        )}
      </div>

      <div className="grid grid-cols-3 gap-3 pt-1" style={{ borderTop: '1px solid var(--hairline)' }}>
        <Stat label="Clients" value={stat('num_sta')} />
        <Stat label="Satisfaction" value={stat('satisfaction')} />
        <Stat label="TX retries" value={stat('tx_retries')} />
      </div>
    </Card>
  );
}
