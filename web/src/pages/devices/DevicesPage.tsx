import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAsync } from '../../api';
import {
  Card,
  DataTable,
  EmptyState,
  Skeleton,
  fmt,
  cn,
  type Column,
} from '../../components/ui';
import { listDevices, type DeviceRollup } from './api';
import { sampleMap } from './metrics';
import { FilterInput, IssueCountBadges } from './parts';

/**
 * /devices — the AP / switch / gateway inventory (docs §Devices). One row per
 * infrastructure device: link state, model, firmware, a type-appropriate load
 * summary, and its open-issue counts. Per-radio channel/power and per-port
 * detail live one level down on /devices/:id (navigate for sub-structure,
 * docs §Interaction); the list stays a single API call and stays scannable.
 */

const TYPE_LABEL: Record<string, string> = {
  ap: 'Access point',
  switch: 'Switch',
  gateway: 'Gateway',
};

function DeviceStatus({ device }: { device: DeviceRollup }) {
  const raw = device.state?.state;
  let color = 'var(--sev-neutral)';
  let label = 'Monitored';
  let title = 'Reachable via probes';
  if (raw != null) {
    const up = raw === '1';
    color = up ? 'var(--sev-healthy)' : 'var(--sev-p1)';
    label = up ? 'Up' : 'Down';
    title = up ? 'Connected' : `state ${raw}`;
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 t-body whitespace-nowrap"
      style={{ color: 'var(--fg)' }}
      title={title}
    >
      <span
        aria-hidden
        className="inline-block w-2 h-2 rounded-full shrink-0"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}

/** A one-line load summary appropriate to the device type. */
function summaryOf(device: DeviceRollup): string {
  const m = sampleMap(device.metrics);
  const num = (k: string) => m.get(k)?.value ?? null;
  if (device.type === 'ap') {
    const sta = num('num_sta');
    const sat = num('satisfaction');
    const parts: string[] = [];
    if (sta != null) parts.push(`${fmt(sta, 0)} clients`);
    if (sat != null) parts.push(`${fmt(sat, 0)}% sat`);
    return parts.join(' · ') || '—';
  }
  if (device.type === 'gateway') {
    const rtt = num('gw_rtt_ms');
    return rtt != null ? `${fmt(rtt, 1)} ms RTT` : '—';
  }
  const cpu = num('cpu');
  const mem = num('mem');
  const parts: string[] = [];
  if (cpu != null) parts.push(`${fmt(cpu, 0)}% CPU`);
  if (mem != null) parts.push(`${fmt(mem, 0)}% mem`);
  return parts.join(' · ') || '—';
}

function statusRank(d: DeviceRollup): number {
  const raw = d.state?.state;
  if (raw == null) return 1;
  return raw === '1' ? 2 : 0; // Down sorts first (most urgent)
}

export function DevicesPage() {
  const navigate = useNavigate();
  const { data, loading, error } = useAsync(listDevices, []);
  const [query, setQuery] = useState('');

  const devices = useMemo(() => data?.devices ?? [], [data]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        (d.model ?? '').toLowerCase().includes(q) ||
        d.type.toLowerCase().includes(q),
    );
  }, [devices, query]);

  const columns: Column<DeviceRollup>[] = [
    {
      key: 'status',
      header: 'Status',
      render: (d) => <DeviceStatus device={d} />,
      sortAccessor: statusRank,
      width: 120,
    },
    {
      key: 'name',
      header: 'Device',
      render: (d) => (
        <div className="flex flex-col">
          <span className="t-body" style={{ color: 'var(--fg)' }}>
            {d.name}
          </span>
          <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
            {TYPE_LABEL[d.type] ?? d.type}
            {d.model ? ` · ${d.model}` : ''}
          </span>
        </div>
      ),
      sortAccessor: (d) => d.name.toLowerCase(),
    },
    {
      key: 'firmware',
      header: 'Firmware',
      render: (d) => (
        <span className="font-mono t-secondary" style={{ color: 'var(--fg-muted)' }}>
          {d.state?.firmware ?? '—'}
        </span>
      ),
      sortAccessor: (d) => d.state?.firmware ?? '',
    },
    {
      key: 'summary',
      header: 'Load',
      render: (d) => (
        <span className="t-secondary tnum" style={{ color: 'var(--fg-muted)' }}>
          {summaryOf(d)}
        </span>
      ),
      sortAccessor: (d) => sampleMap(d.metrics).get('num_sta')?.value ?? -1,
    },
    {
      key: 'issues',
      header: 'Issues',
      align: 'right',
      render: (d) => (
        <div className="flex justify-end">
          <IssueCountBadges counts={d.issue_counts} />
        </div>
      ),
      sortAccessor: (d) =>
        d.issue_counts.p1 * 1_000_000 + d.issue_counts.p2 * 1_000 + d.issue_counts.p3,
      width: 120,
    },
  ];

  return (
    <div className="px-6 lg:px-8 py-6 max-w-[1100px] mx-auto">
      <div className="flex items-center justify-between gap-4 mb-5">
        <div>
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Devices
          </h2>
          <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            {loading && !data
              ? 'Loading inventory…'
              : `${devices.length} access points, switches, and gateways`}
          </p>
        </div>
        {devices.length > 0 && (
          <FilterInput value={query} onChange={setQuery} placeholder="Filter devices" />
        )}
      </div>

      {loading && !data ? (
        <Card>
          <div className="flex flex-col gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} width="100%" height={40} />
            ))}
          </div>
        </Card>
      ) : error ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="Could not load devices"
            description={`The inventory request failed (${error.status || 'network error'}).`}
          />
        </Card>
      ) : devices.length === 0 ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="No devices yet"
            description="The daemon has not recorded any infrastructure devices. They appear here after the first successful poll."
          />
        </Card>
      ) : (
        <Card pad="none" className={cn('overflow-hidden')}>
          <DataTable
            columns={columns}
            rows={filtered}
            rowKey={(d) => d.entity_id}
            onRowActivate={(d) => navigate(`/devices/${d.entity_id}`)}
            initialSort={{ key: 'status', dir: 'asc' }}
            empty={
              <EmptyState
                variant="no-match"
                title="No devices match"
                description={`Nothing matches “${query}”.`}
                action={{ label: 'Clear filter', onClick: () => setQuery('') }}
              />
            }
          />
        </Card>
      )}
    </div>
  );
}
