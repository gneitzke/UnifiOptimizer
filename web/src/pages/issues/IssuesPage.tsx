import { useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Search } from 'lucide-react';
import { DataTable, type Column } from '../../components/ui/DataTable';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useRegisterFilter } from '../../layout/keyboard/filterFocusContext';
import { useWsFrames } from '../../api/WsProvider';
import { EntityLink } from '../shared/EntityLink';
import {
  issueDurationSeconds,
  ongoingLabel,
  severityRank,
  stateRank,
} from '../shared/format';
import { listIssues, entityLabel, type IssueRow } from '../shared/api';
import { usePageAsync, useNowSeconds } from '../shared/hooks';
import type { Severity } from '../../api/types';

/**
 * Issues list (`/issues`) — the product's heart. A filterable, sortable table
 * over every issue: severity + state pills, the owning entity, the detector, an
 * "ongoing 6d" duration, and last-seen. `/` focuses the text filter; j/k + Enter
 * traverse and open rows (via DataTable's list-navigation). Filters live in the
 * URL so a dashboard link like `?severity=p1&state=active` lands pre-filtered.
 */

type StateFilter = 'open' | 'active' | 'resolved' | 'all';
const STATE_OPTIONS: { value: StateFilter; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'active', label: 'Active' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'all', label: 'All' },
];
const SEV_OPTIONS: { value: '' | Severity; label: string }[] = [
  { value: '', label: 'All severities' },
  { value: 'p1', label: 'P1 critical' },
  { value: 'p2', label: 'P2 major' },
  { value: 'p3', label: 'P3 minor' },
];

function stateMatches(state: string, filter: StateFilter): boolean {
  if (filter === 'all') return true;
  if (filter === 'open') return state !== 'resolved';
  return state === filter;
}

export function IssuesPage() {
  const navigate = useNavigate();
  const registerFilter = useRegisterFilter();
  const [params, setParams] = useSearchParams();

  const stateFilter = (params.get('state') as StateFilter) || 'open';
  const sevFilter = (params.get('severity') as Severity | null) ?? '';
  const query = params.get('q') ?? '';

  const { data, loading, error, reload } = usePageAsync(() => listIssues(), [], {
    pollMs: 30_000,
  });
  useWsFrames((frame) => {
    if (frame.type === 'issue_transition') reload();
  });

  const now = useNowSeconds();

  const patch = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === '') p.delete(k);
      else p.set(k, v);
    }
    setParams(p, { replace: true });
  };

  const filtered = useMemo(() => {
    const all = data?.issues ?? [];
    const q = query.trim().toLowerCase();
    const rows = all.filter((i) => {
      if (!stateMatches(i.state, stateFilter)) return false;
      if (sevFilter && i.severity !== sevFilter) return false;
      if (q) {
        const hay = `${i.title} ${i.detector_key} ${entityLabel(i.entity)}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    // Default order: most severe first, then longest-running.
    return rows.sort((a, b) => {
      const s = severityRank(a.severity) - severityRank(b.severity);
      if (s !== 0) return s;
      return issueDurationSeconds(b, now) - issueDurationSeconds(a, now);
    });
  }, [data, stateFilter, sevFilter, query, now]);

  const columns: Column<IssueRow>[] = useMemo(
    () => [
      {
        key: 'severity',
        header: 'Sev',
        width: 64,
        sortAccessor: (r) => severityRank(r.severity),
        render: (r) => <SeverityPill severity={r.severity} />,
      },
      {
        key: 'state',
        header: 'State',
        width: 108,
        sortAccessor: (r) => stateRank(r.state),
        render: (r) => <StatePill state={r.state} severity={r.severity} />,
      },
      {
        key: 'title',
        header: 'Issue',
        sortAccessor: (r) => r.title.toLowerCase(),
        render: (r) => (
          <span className="block truncate max-w-[36ch]" style={{ color: 'var(--fg)' }}>
            {r.title}
          </span>
        ),
      },
      {
        key: 'entity',
        header: 'Entity',
        sortAccessor: (r) => entityLabel(r.entity).toLowerCase(),
        render: (r) =>
          r.entity ? (
            <EntityLink entity={r.entity} />
          ) : (
            <span style={{ color: 'var(--fg-subtle)' }}>network-wide</span>
          ),
      },
      {
        key: 'detector',
        header: 'Detector',
        sortAccessor: (r) => r.detector_key,
        render: (r) => (
          <code className="t-caption" style={{ color: 'var(--fg-muted)' }}>
            {r.detector_key}
          </code>
        ),
      },
      {
        key: 'duration',
        header: 'Duration',
        numeric: true,
        align: 'left',
        width: 130,
        sortAccessor: (r) => issueDurationSeconds(r, now),
        render: (r) => (
          <span className="tnum" style={{ color: 'var(--fg-muted)' }}>
            {ongoingLabel(r, now)}
          </span>
        ),
      },
      {
        key: 'last_seen',
        header: 'Last seen',
        numeric: true,
        align: 'left',
        width: 110,
        sortAccessor: (r) => r.last_seen_ts,
        render: (r) => (
          <RelativeTime
            ts={r.last_seen_ts}
            mode="relative"
            className="t-caption tnum"
          />
        ),
      },
    ],
    [now],
  );

  const total = data?.issues.length ?? 0;
  const hasActiveFilters = !!sevFilter || !!query.trim() || stateFilter !== 'open';

  function renderEmpty() {
    if (total === 0) {
      return (
        <EmptyState
          variant="no-data"
          title="No issues recorded yet"
          description="The daemon opens an issue when a detector's condition holds. Nothing has crossed a threshold yet."
        />
      );
    }
    if (hasActiveFilters) {
      return (
        <EmptyState
          variant="no-match"
          description="No issues match the current filters."
          action={{
            label: 'Clear filters',
            onClick: () => setParams(new URLSearchParams(), { replace: true }),
          }}
        />
      );
    }
    // Default view (open) with nothing open, but resolved history exists.
    return (
      <EmptyState
        variant="healthy"
        title="No open issues"
      />
    );
  }

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1200 }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Issues
          </h2>
          {data && (
            <span className="t-secondary tnum" style={{ color: 'var(--fg-muted)' }}>
              {filtered.length} shown
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="relative flex items-center">
            <Search
              size={14}
              className="absolute left-2.5 pointer-events-none"
              style={{ color: 'var(--fg-subtle)' }}
            />
            <input
              ref={registerFilter}
              type="text"
              value={query}
              placeholder="Filter…  ( / )"
              onChange={(e) => patch({ q: e.target.value })}
              className="h-8 w-52 pl-8 pr-2 rounded-control t-body outline-none"
              style={{
                background: 'var(--surface)',
                border: '1px solid var(--strong)',
                color: 'var(--fg)',
              }}
            />
          </label>

          <select
            value={sevFilter}
            onChange={(e) => patch({ severity: e.target.value || null })}
            className="h-8 px-2 rounded-control t-body cursor-pointer outline-none"
            style={{ background: 'var(--surface)', border: '1px solid var(--strong)', color: 'var(--fg)' }}
            aria-label="Filter by severity"
          >
            {SEV_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <div
            className="inline-flex rounded-control overflow-hidden"
            style={{ border: '1px solid var(--strong)' }}
            role="group"
            aria-label="Filter by state"
          >
            {STATE_OPTIONS.map((o) => {
              const active = stateFilter === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  onClick={() => patch({ state: o.value === 'open' ? null : o.value })}
                  className="h-8 px-2.5 t-caption cursor-pointer transition-colors"
                  style={{
                    background: active ? 'var(--accent)' : 'transparent',
                    color: active ? 'var(--accent-fg)' : 'var(--fg-muted)',
                  }}
                >
                  {o.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {error ? (
        <EmptyState
          variant="no-data"
          title="Could not load issues"
          description="The daemon may still be starting, or the API is unreachable."
        />
      ) : loading && !data ? (
        <div className="flex flex-col gap-2 pt-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-11 w-full" />
          ))}
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(r) => r.id}
          rowHeight={44}
          onRowActivate={(r) => navigate(`/issues/${r.id}`)}
          empty={renderEmpty()}
        />
      )}
    </div>
  );
}
