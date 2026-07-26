import { DataTable, fmt, type Column } from '../../components/ui';
import type { ChildEntity } from './api';
import { sampleMap } from './metrics';

/**
 * Per-port table for a switch (docs §Devices/:id: "per-port table — speed +
 * duplex + PoE + error rates"). Link state, negotiated speed/duplex, PoE draw,
 * and RX/TX error and drop counts. Errors (genuine faults, normally zero) are
 * tinted when non-zero; drops are cumulative counters that occur under normal
 * load, so they stay neutral rather than painting an amber wall (color encodes
 * a real problem only — never-do rules 1 & 6).
 */

function metricVal(port: ChildEntity, key: string): number | null {
  return sampleMap(port.metrics).get(key)?.value ?? null;
}

/** Natural port order (Port 1, 2, … 10) from the trailing number in the name. */
function portOrder(port: ChildEntity): number {
  const digits = /(\d+)\s*$/.exec(port.name);
  const n = digits ? Number(digits[1]) : 0;
  // SFP ports sort after copper ports.
  return /sfp/i.test(port.name) ? 1000 + n : n;
}

function CountCell({ value, warn }: { value: number | null; warn?: boolean }) {
  if (value == null) return <span style={{ color: 'var(--fg-subtle)' }}>—</span>;
  const flag = warn && value > 0;
  return (
    <span className="tnum" style={{ color: flag ? 'var(--sev-p2)' : 'var(--fg-muted)' }}>
      {fmt(value, 0)}
    </span>
  );
}

function speedDuplex(port: ChildEntity): string {
  const up = port.state?.up === 'True';
  if (!up) return 'down';
  const speed = port.state?.speed;
  const fd = port.state?.full_duplex === 'True';
  if (speed == null) return 'up';
  const s = Number(speed);
  const label = s >= 1000 ? `${s / 1000} Gb` : `${s} Mb`;
  return `${label} ${fd ? 'FD' : 'HD'}`;
}

export function PortTable({ ports }: { ports: ChildEntity[] }) {
  const columns: Column<ChildEntity>[] = [
    {
      key: 'port',
      header: 'Port',
      render: (p) => {
        const up = p.state?.up === 'True';
        const isUplink = Boolean((p.meta as { is_uplink?: boolean }).is_uplink);
        return (
          <span className="inline-flex items-center gap-2">
            <span
              aria-hidden
              className="inline-block w-2 h-2 rounded-full shrink-0"
              style={{ background: up ? 'var(--sev-healthy)' : 'var(--sev-neutral)' }}
              title={up ? 'Link up' : 'Link down'}
            />
            <span className="t-body" style={{ color: 'var(--fg)' }}>
              {p.name}
            </span>
            {isUplink && (
              <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                uplink
              </span>
            )}
          </span>
        );
      },
      sortAccessor: portOrder,
    },
    {
      key: 'link',
      header: 'Speed / duplex',
      render: (p) => (
        <span className="t-secondary tnum" style={{ color: 'var(--fg-muted)' }}>
          {speedDuplex(p)}
        </span>
      ),
      sortAccessor: (p) => Number(p.state?.speed ?? -1),
    },
    {
      key: 'poe',
      header: 'PoE',
      numeric: true,
      align: 'right',
      render: (p) => {
        const w = metricVal(p, 'poe_power');
        return (
          <span style={{ color: w ? 'var(--fg)' : 'var(--fg-subtle)' }}>
            {w == null || w === 0 ? '—' : `${fmt(w, 1)} W`}
          </span>
        );
      },
      sortAccessor: (p) => metricVal(p, 'poe_power') ?? -1,
    },
    {
      key: 'rx_err',
      header: 'RX err',
      numeric: true,
      align: 'right',
      render: (p) => <CountCell value={metricVal(p, 'rx_errors')} warn />,
      sortAccessor: (p) => metricVal(p, 'rx_errors') ?? -1,
    },
    {
      key: 'tx_err',
      header: 'TX err',
      numeric: true,
      align: 'right',
      render: (p) => <CountCell value={metricVal(p, 'tx_errors')} warn />,
      sortAccessor: (p) => metricVal(p, 'tx_errors') ?? -1,
    },
    {
      key: 'rx_drop',
      header: 'RX drop',
      numeric: true,
      align: 'right',
      render: (p) => <CountCell value={metricVal(p, 'rx_dropped')} />,
      sortAccessor: (p) => metricVal(p, 'rx_dropped') ?? -1,
    },
    {
      key: 'tx_drop',
      header: 'TX drop',
      numeric: true,
      align: 'right',
      render: (p) => <CountCell value={metricVal(p, 'tx_dropped')} />,
      sortAccessor: (p) => metricVal(p, 'tx_dropped') ?? -1,
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={ports}
      rowKey={(p) => p.entity_id}
      initialSort={{ key: 'port', dir: 'asc' }}
    />
  );
}
