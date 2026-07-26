import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Cable, Wifi } from 'lucide-react';
import { useAsync } from '../../api';
import {
  Card,
  DataTable,
  EmptyState,
  Skeleton,
  fmt,
  type Column,
} from '../../components/ui';
import { listClients, listDevices, type ClientRollup } from '../devices/api';
import { sampleMap } from '../devices/metrics';
import { FilterInput, IssueCountBadges } from '../devices/parts';

/**
 * /clients — every client with its connection, access point, signal, and roam
 * activity (docs §Clients). A Show-all / Active toggle hides clients not seen in
 * the latest poll so the default view is the network as it is now. The parent AP
 * / switch name is resolved from the device inventory (the rollup carries only a
 * parent id). Satisfaction and RSSI history live on the client detail — a
 * per-row sparkline would mean one window request per client, so the list stays
 * a single call and the trend charts render one level down.
 */

/** Seen within this many seconds of the latest observed poll counts as active. */
const ACTIVE_WINDOW_S = 900;

function isWired(c: ClientRollup): boolean {
  return Boolean((c.meta as { is_wired?: boolean }).is_wired);
}

function connectionLabel(c: ClientRollup): string {
  if (isWired(c)) return 'Wired';
  const essid = (c.meta as { essid?: string }).essid;
  return essid || 'Wi‑Fi';
}

export function ClientsPage() {
  const navigate = useNavigate();
  const clientsReq = useAsync(listClients, []);
  const devicesReq = useAsync(listDevices, []);
  const [query, setQuery] = useState('');
  const [activeOnly, setActiveOnly] = useState(true);

  const clients = useMemo(() => clientsReq.data?.clients ?? [], [clientsReq.data]);

  const apName = useMemo(() => {
    const map = new Map<number, string>();
    for (const d of devicesReq.data?.devices ?? []) map.set(d.entity_id, d.name);
    return map;
  }, [devicesReq.data]);

  // "Active" = seen in the latest poll; measured against the freshest client
  // timestamp so it is robust to how old the snapshot is.
  const latestPoll = useMemo(
    () => clients.reduce((mx, c) => Math.max(mx, c.last_seen_ts), 0),
    [clients],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return clients.filter((c) => {
      if (activeOnly && latestPoll && c.last_seen_ts < latestPoll - ACTIVE_WINDOW_S) {
        return false;
      }
      if (!q) return true;
      return (
        c.name.toLowerCase().includes(q) ||
        c.native_id.toLowerCase().includes(q) ||
        connectionLabel(c).toLowerCase().includes(q)
      );
    });
  }, [clients, query, activeOnly, latestPoll]);

  const activeCount = useMemo(
    () =>
      latestPoll
        ? clients.filter((c) => c.last_seen_ts >= latestPoll - ACTIVE_WINDOW_S).length
        : clients.length,
    [clients, latestPoll],
  );

  const columns: Column<ClientRollup>[] = [
    {
      key: 'name',
      header: 'Client',
      render: (c) => (
        <div className="flex items-center gap-2 min-w-0">
          {isWired(c) ? (
            <Cable size={15} style={{ color: 'var(--fg-subtle)' }} aria-label="Wired" />
          ) : (
            <Wifi size={15} style={{ color: 'var(--fg-subtle)' }} aria-label="Wi‑Fi" />
          )}
          <div className="flex flex-col min-w-0">
            <span className="t-body truncate" style={{ color: 'var(--fg)' }}>
              {c.name}
            </span>
            <span className="t-caption truncate" style={{ color: 'var(--fg-subtle)' }}>
              {connectionLabel(c)}
            </span>
          </div>
        </div>
      ),
      sortAccessor: (c) => c.name.toLowerCase(),
    },
    {
      key: 'ap',
      header: 'Connected to',
      render: (c) => (
        <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          {c.parent_id != null ? apName.get(c.parent_id) ?? '—' : '—'}
        </span>
      ),
      sortAccessor: (c) => (c.parent_id != null ? apName.get(c.parent_id) ?? '' : ''),
    },
    {
      key: 'rssi',
      header: 'RSSI (dBm)',
      numeric: true,
      align: 'right',
      render: (c) => {
        const v = sampleMap(c.metrics).get('rssi')?.value ?? null;
        return <span style={{ color: v == null ? 'var(--fg-subtle)' : 'var(--fg)' }}>{v == null ? '—' : fmt(v, 0)}</span>;
      },
      sortAccessor: (c) => sampleMap(c.metrics).get('rssi')?.value ?? -999,
    },
    {
      key: 'satisfaction',
      header: 'Sat (%)',
      numeric: true,
      align: 'right',
      render: (c) => {
        const v = sampleMap(c.metrics).get('satisfaction')?.value ?? null;
        return <span style={{ color: v == null ? 'var(--fg-subtle)' : 'var(--fg)' }}>{v == null ? '—' : fmt(v, 0)}</span>;
      },
      sortAccessor: (c) => sampleMap(c.metrics).get('satisfaction')?.value ?? -1,
    },
    {
      key: 'roams',
      header: 'Roams',
      numeric: true,
      align: 'right',
      render: (c) => {
        const v = sampleMap(c.metrics).get('roam_count')?.value ?? null;
        return <span style={{ color: v ? 'var(--fg)' : 'var(--fg-subtle)' }}>{v == null ? '—' : fmt(v, 0)}</span>;
      },
      sortAccessor: (c) => sampleMap(c.metrics).get('roam_count')?.value ?? -1,
    },
    {
      key: 'issues',
      header: 'Issues',
      align: 'right',
      render: (c) => (
        <div className="flex justify-end">
          <IssueCountBadges counts={c.issue_counts} />
        </div>
      ),
      sortAccessor: (c) =>
        c.issue_counts.p1 * 1_000_000 + c.issue_counts.p2 * 1_000 + c.issue_counts.p3,
      width: 110,
    },
  ];

  const loading = clientsReq.loading && !clientsReq.data;

  return (
    <div className="px-6 lg:px-8 py-6 max-w-[1100px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-5">
        <div>
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Clients
          </h2>
          <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            {loading
              ? 'Loading clients…'
              : `${activeCount} active · ${clients.length} seen`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ActiveToggle activeOnly={activeOnly} onChange={setActiveOnly} />
          {clients.length > 0 && (
            <FilterInput value={query} onChange={setQuery} placeholder="Filter clients" />
          )}
        </div>
      </div>

      {loading ? (
        <Card>
          <div className="flex flex-col gap-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} width="100%" height={40} />
            ))}
          </div>
        </Card>
      ) : clientsReq.error ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="Could not load clients"
            description={`The request failed (${clientsReq.error.status || 'network error'}).`}
          />
        </Card>
      ) : clients.length === 0 ? (
        <Card>
          <EmptyState
            variant="no-data"
            title="No clients yet"
            description="Clients appear here once the daemon has seen them associate."
          />
        </Card>
      ) : (
        <Card pad="none" className="overflow-hidden">
          <DataTable
            columns={columns}
            rows={filtered}
            rowKey={(c) => c.entity_id}
            onRowActivate={(c) => navigate(`/clients/${c.entity_id}`)}
            initialSort={{ key: 'name', dir: 'asc' }}
            empty={
              <EmptyState
                variant="no-match"
                title={activeOnly ? 'No active clients match' : 'No clients match'}
                description={
                  query
                    ? `Nothing matches “${query}”.`
                    : 'No clients were seen in the latest poll.'
                }
                action={
                  activeOnly
                    ? { label: 'Show all clients', onClick: () => setActiveOnly(false) }
                    : query
                      ? { label: 'Clear filter', onClick: () => setQuery('') }
                      : undefined
                }
              />
            }
          />
        </Card>
      )}
    </div>
  );
}

function ActiveToggle({
  activeOnly,
  onChange,
}: {
  activeOnly: boolean;
  onChange: (v: boolean) => void;
}) {
  const opts: Array<{ label: string; value: boolean }> = [
    { label: 'Active', value: true },
    { label: 'All', value: false },
  ];
  return (
    <div
      role="tablist"
      aria-label="Client visibility"
      className="inline-flex rounded-control p-0.5"
      style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
    >
      {opts.map((o) => {
        const active = activeOnly === o.value;
        return (
          <button
            key={o.label}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className="h-8 px-3 rounded-[6px] t-caption font-medium transition-colors cursor-pointer"
            style={{
              background: active ? 'var(--surface)' : 'transparent',
              color: active ? 'var(--fg)' : 'var(--fg-muted)',
              boxShadow: active ? 'var(--shadow-card)' : undefined,
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
