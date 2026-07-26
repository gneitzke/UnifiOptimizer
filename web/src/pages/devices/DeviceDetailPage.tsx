import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAsync } from '../../api';
import {
  Card,
  EmptyState,
  Skeleton,
  RelativeTime,
  fmt,
} from '../../components/ui';
import { getDevice, type ChildEntity, type DeviceDetail } from './api';
import { sampleMap, metricMeta } from './metrics';
import { MetricChart } from './MetricChart';
import { RadioCard } from './RadioCard';
import { PortTable } from './PortTable';
import { StateHistory } from './StateHistory';
import { IssueMiniList } from './IssueMiniList';
import { InfoRow, RangeToggle, RANGE_24H, SectionTitle } from './parts';
import { useMeasuredWidth } from '../report/charts/useMeasuredWidth';

/**
 * /devices/:id — one device in full (docs §Devices/:id). Header with live state,
 * firmware, and a state-change timeline; per-radio cards or a per-port table;
 * metric charts with a 24h/7d toggle backed by /api/metrics/window; and the
 * issues, past and present, that concern this device.
 */

const TYPE_LABEL: Record<string, string> = {
  ap: 'Access point',
  switch: 'Switch',
  gateway: 'Gateway',
};

/** Which metrics to chart, intersected with what the device actually reports. */
function chartMetricsFor(device: DeviceDetail): string[] {
  const present = new Set(device.metrics.map((s) => s.metric));
  const preferred: Record<string, string[]> = {
    ap: ['satisfaction', 'num_sta', 'cpu', 'mem'],
    switch: ['cpu', 'mem'],
    gateway: ['gw_rtt_ms', 'dns_latency_ms', 'dns_anchor_latency_ms'],
  };
  const list = preferred[device.type] ?? [];
  return list.filter((k) => present.has(k));
}

// Below this column width a radio card's fixed 120px sparkline starts visibly
// compressing (its flex row has nowhere else to give), so the grid never picks a
// column count that would squeeze a card thinner than this. The 3-column
// Clients/Satisfaction/TX-retries stat row stays legible even tighter than this,
// because its labels wrap instead of overflowing (see RadioCard's `Stat`).
const RADIO_MIN_COL = 200;
const RADIO_GRID_GAP = 16; // matches the grid's `gap-4`

/**
 * Columns for the radio grid: prefer laying every radio out in one row (1, 2, 3,
 * up to 4 across), but only once each column clears RADIO_MIN_COL, and never a
 * count that stacks evenly except for one lonely card on its own row — for 3
 * radios that means 3-across or a single full-width column, never a 2-then-1.
 */
function radioGridColumns(n: number, width: number): number {
  if (n <= 1) return 1;
  const fits = (k: number) => k * RADIO_MIN_COL + (k - 1) * RADIO_GRID_GAP <= width;
  for (let k = Math.min(n, 4); k >= 1; k--) {
    if (k > 1 && n % k === 1) continue; // would strand exactly one card
    if (fits(k)) return k;
  }
  return 1;
}

function RadioGrid({ radios }: { radios: ChildEntity[] }) {
  const [ref, width] = useMeasuredWidth();
  const cols = radioGridColumns(radios.length, width);
  return (
    <div
      ref={ref}
      className="grid gap-4"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {radios.map((r) => (
        <RadioCard key={r.entity_id} radio={r} />
      ))}
    </div>
  );
}

function StatusDot({ device }: { device: DeviceDetail }) {
  const raw = device.state?.state;
  let color = 'var(--sev-neutral)';
  let label = 'Monitored';
  if (raw != null) {
    const up = raw === '1';
    color = up ? 'var(--sev-healthy)' : 'var(--sev-p1)';
    label = up ? 'Up' : 'Down';
  }
  return (
    <span className="inline-flex items-center gap-1.5 t-body" style={{ color: 'var(--fg)' }}>
      <span
        aria-hidden
        className="inline-block w-2.5 h-2.5 rounded-full"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

export function DeviceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const entityId = Number(id);
  const { data, loading, error } = useAsync(() => getDevice(entityId), [entityId]);
  const [range, setRange] = useState(RANGE_24H);

  const back = (
    <Link
      to="/devices"
      className="inline-flex items-center gap-1.5 t-secondary mb-4 hover:underline"
      style={{ color: 'var(--fg-muted)' }}
    >
      <ArrowLeft size={15} /> Devices
    </Link>
  );

  const wrap = (children: React.ReactNode) => (
    <div className="px-6 lg:px-8 py-6 max-w-[1100px] mx-auto">{children}</div>
  );

  if (loading && !data) {
    return wrap(
      <>
        {back}
        <Skeleton width={240} height={30} />
        <Card className="mt-4">
          <Skeleton width="100%" height={120} />
        </Card>
      </>,
    );
  }

  if (error) {
    return wrap(
      <>
        {back}
        <Card>
          <EmptyState
            variant="no-data"
            title={error.status === 404 ? 'Device not found' : 'Could not load device'}
            description={
              error.status === 404
                ? 'This device id is not in the inventory. It may have been removed.'
                : `The request failed (${error.status || 'network error'}).`
            }
          />
        </Card>
      </>,
    );
  }

  const device = data!;
  const radios = device.children.filter((c) => c.type === 'radio');
  const ports = device.children.filter((c) => c.type === 'port');
  const chartMetrics = chartMetricsFor(device);
  const m = sampleMap(device.metrics);

  return wrap(
    <>
      {back}

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div className="min-w-0">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            {device.name}
          </h2>
          <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            {TYPE_LABEL[device.type] ?? device.type}
            {device.model ? ` · ${device.model}` : ''}
          </p>
        </div>
        <StatusDot device={device} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Meta + state history (left rail) */}
        <div className="flex flex-col gap-4">
          <Card>
            <SectionTitle>Overview</SectionTitle>
            <div className="flex flex-col" style={{ marginTop: -4 }}>
              <InfoRow label="Firmware">
                <span className="font-mono">{device.state?.firmware ?? '—'}</span>
              </InfoRow>
              {device.state?.uplink_type && (
                <InfoRow label="Uplink">{device.state.uplink_type}</InfoRow>
              )}
              {m.get('uplink_rssi')?.value != null && (
                <InfoRow label="Uplink RSSI">
                  {fmt(m.get('uplink_rssi')!.value, 0)} dBm
                </InfoRow>
              )}
              <InfoRow label="MAC">
                <span className="font-mono t-secondary">{device.native_id}</span>
              </InfoRow>
              <InfoRow label="First seen">
                <RelativeTime ts={device.first_seen_ts} mode="relative" />
              </InfoRow>
              <InfoRow label="Last update">
                <RelativeTime ts={device.last_seen_ts} mode="relative" />
              </InfoRow>
            </div>
          </Card>

          <Card>
            <SectionTitle>State history</SectionTitle>
            <StateHistory changes={device.state_changes} />
          </Card>
        </div>

        {/* Radios / ports + charts (main column) */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          {radios.length > 0 && (
            <section>
              <SectionTitle>Radios</SectionTitle>
              <RadioGrid radios={radios} />
            </section>
          )}

          {ports.length > 0 && (
            <section>
              <SectionTitle>Ports</SectionTitle>
              <Card pad="none" className="overflow-hidden">
                <PortTable ports={ports} />
              </Card>
            </section>
          )}

          {chartMetrics.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-3">
                <h3 className="t-section" style={{ color: 'var(--fg)' }}>
                  Metrics
                </h3>
                <RangeToggle value={range} onChange={setRange} />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                {chartMetrics.map((metric) => (
                  <MetricChart
                    key={metric}
                    entityId={device.entity_id}
                    metric={metric}
                    seconds={range}
                  />
                ))}
              </div>
              <p className="t-caption mt-2" style={{ color: 'var(--fg-subtle)' }}>
                {chartMetrics.map((k) => metricMeta(k).label).join(', ')} over the last{' '}
                {range === RANGE_24H ? '24 hours' : '7 days'}.
              </p>
            </section>
          )}
        </div>
      </div>

      {/* Issues, full width */}
      <section className="mt-4">
        <Card>
          <SectionTitle>Issues</SectionTitle>
          <IssueMiniList open={device.issues_open} resolved={device.issues_resolved} />
        </Card>
      </section>
    </>,
  );
}
