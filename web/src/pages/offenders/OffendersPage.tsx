import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { DataTable, type Column } from '../../components/ui/DataTable';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { EntityLink } from '../shared/EntityLink';
import {
  listClientOffenders,
  listDeviceOffenders,
  entityLabel,
  type OffenderRow,
} from '../shared/api';
import { usePageAsync } from '../shared/hooks';

/**
 * Offenders (`/offenders`) — the "who causes most of my grief" leaderboard (§17).
 * Entities ranked by a composite burden: failed SLE client-minutes attributed to
 * them, open issues weighted by severity, and disconnect/roam churn over a window.
 * A sortable table (score, issues, fail-minutes, events all sort); a Devices /
 * Clients toggle switches surface. Read-only; the ranking is three store GROUP BYs.
 */

type Surface = 'devices' | 'clients';
const WINDOW_OPTIONS = [
  { value: 86_400, label: '24 hours' },
  { value: 7 * 86_400, label: '7 days' },
  { value: 30 * 86_400, label: '30 days' },
];
const TOP_N = 50;

export function OffendersPage() {
  const [params, setParams] = useSearchParams();
  const surface = (params.get('surface') as Surface) || 'devices';
  const windowS = Number(params.get('window_s')) || 86_400;

  const { data, loading, error } = usePageAsync(
    () =>
      surface === 'devices'
        ? listDeviceOffenders(windowS, TOP_N)
        : listClientOffenders(windowS, TOP_N),
    [surface, windowS],
    { pollMs: 60_000 },
  );

  const patch = (next: Record<string, string>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) p.set(k, v);
    setParams(p, { replace: true });
  };

  const rows = data?.offenders ?? [];

  const columns: Column<OffenderRow>[] = useMemo(
    () => [
      {
        key: 'entity',
        header: surface === 'devices' ? 'Device' : 'Client',
        sortAccessor: (r) => entityLabel(r.entity).toLowerCase(),
        render: (r) => <EntityLink entity={r.entity} />,
      },
      {
        key: 'score',
        header: 'Burden score',
        numeric: true,
        width: 130,
        sortAccessor: (r) => r.score,
        render: (r) => (
          <span className="tnum" style={{ color: 'var(--fg)' }}>
            {Math.round(r.score)}
          </span>
        ),
      },
      {
        key: 'issues',
        header: 'Open issues',
        numeric: true,
        width: 120,
        sortAccessor: (r) => r.issue_counts.total,
        render: (r) => <IssueBreakdown counts={r.issue_counts} />,
      },
      {
        key: 'fail_minutes',
        header: 'Fail-minutes',
        numeric: true,
        width: 120,
        sortAccessor: (r) => r.fail_minutes,
        render: (r) => (
          <span className="tnum" style={{ color: 'var(--fg-muted)' }}>
            {Math.round(r.fail_minutes)}
          </span>
        ),
      },
      {
        key: 'events',
        header: 'Disconnect/roam',
        numeric: true,
        width: 140,
        sortAccessor: (r) => r.event_count,
        render: (r) => (
          <span className="tnum" style={{ color: 'var(--fg-muted)' }}>
            {r.event_count}
          </span>
        ),
      },
    ],
    [surface],
  );

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1000 }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Top offenders
          </h2>
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            Ranked by attributed failed client-minutes, severity-weighted open issues, and
            disconnect/roam churn.
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div
            className="inline-flex rounded-control overflow-hidden"
            style={{ border: '1px solid var(--strong)' }}
            role="group"
            aria-label="Offender surface"
          >
            {(['devices', 'clients'] as Surface[]).map((s) => {
              const active = surface === s;
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => patch({ surface: s })}
                  className="h-8 px-3 t-caption cursor-pointer transition-colors capitalize"
                  style={{
                    background: active ? 'var(--accent)' : 'transparent',
                    color: active ? 'var(--accent-fg)' : 'var(--fg-muted)',
                  }}
                >
                  {s}
                </button>
              );
            })}
          </div>

          <select
            value={windowS}
            onChange={(e) => patch({ window_s: e.target.value })}
            className="h-8 px-2 rounded-control t-body cursor-pointer outline-none"
            style={{ background: 'var(--surface)', border: '1px solid var(--strong)', color: 'var(--fg)' }}
            aria-label="Window"
          >
            {WINDOW_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {data && (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          {rows.length} ranked · <RelativeTime ts={data.end_ts} mode="as-of" />
        </span>
      )}

      {error ? (
        <EmptyState
          variant="no-data"
          title="Could not load offenders"
          description="The daemon may still be starting, or the API is unreachable."
        />
      ) : loading && !data ? (
        <div className="flex flex-col gap-2 pt-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.entity_id}
          initialSort={{ key: 'score', dir: 'desc' }}
          empty={
            <EmptyState
              variant="healthy"
              title={`No problem ${surface}`}
              description="No entity crossed a measurable burden threshold in this window."
            />
          }
        />
      )}
    </div>
  );
}

function IssueBreakdown({ counts }: { counts: { p1: number; p2: number; p3: number; total: number } }) {
  if (counts.total === 0) {
    return <span className="tnum" style={{ color: 'var(--fg-subtle)' }}>0</span>;
  }
  const parts: string[] = [];
  if (counts.p1) parts.push(`${counts.p1}×P1`);
  if (counts.p2) parts.push(`${counts.p2}×P2`);
  if (counts.p3) parts.push(`${counts.p3}×P3`);
  return (
    <span className="tnum t-caption" style={{ color: 'var(--fg-muted)' }} title={parts.join(' · ')}>
      {counts.total}
    </span>
  );
}
