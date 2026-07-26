import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Card } from '../../components/ui/Card';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { useWsFrames } from '../../api/WsProvider';
import { severityRank } from '../shared/format';
import { listIncidents } from '../shared/api';
import { usePageAsync, useNowSeconds } from '../shared/hooks';
import { IncidentRow } from './IncidentRow';
import type { Severity } from '../../api/types';

/**
 * Incidents list (`/incidents`). §17: the dashboard leads with incidents ("3
 * things need attention, not 11 scattered issues"), but the dashboard shows only
 * a preview; drilling past it must NOT dump the operator back into the flat,
 * ungrouped issue table. This is the full incident-grouped view — every incident,
 * severity-ranked, with the same root-cause line + "+N related" expander as the
 * dashboard card — so the synthesis §17 exists to provide survives the drill-down.
 */

type StateFilter = 'open' | 'all';

const SEVS: Severity[] = ['p1', 'p2', 'p3'];

export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const stateFilter = (params.get('state') as StateFilter) || 'open';

  const { data, loading, error, reload } = usePageAsync(
    () => listIncidents(stateFilter === 'all'),
    [stateFilter],
    { pollMs: 30_000 },
  );
  useWsFrames((frame) => {
    if (frame.type === 'issue_transition') reload();
  });
  const now = useNowSeconds();

  const sorted = useMemo(
    () =>
      [...(data?.incidents ?? [])].sort((a, b) => {
        // Open before resolved, then most-severe, then longest-running.
        const openA = a.state === 'resolved' ? 1 : 0;
        const openB = b.state === 'resolved' ? 1 : 0;
        if (openA !== openB) return openA - openB;
        const s = severityRank(a.severity) - severityRank(b.severity);
        if (s !== 0) return s;
        return a.first_seen_ts - b.first_seen_ts;
      }),
    [data],
  );

  const counts: Record<Severity, number> = { p1: 0, p2: 0, p3: 0 };
  for (const inc of sorted) if (inc.state !== 'resolved') counts[inc.severity] += 1;
  const grouped = sorted.filter((i) => i.symptom_count > 0).length;

  const patch = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(next)) {
      if (v === null) p.delete(k);
      else p.set(k, v);
    }
    setParams(p, { replace: true });
  };

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1200 }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Incidents
          </h2>
          {data && (
            <span className="t-secondary tnum" style={{ color: 'var(--fg-muted)' }}>
              {sorted.length} shown · {grouped} grouped
            </span>
          )}
        </div>

        <div
          className="inline-flex rounded-control overflow-hidden"
          style={{ border: '1px solid var(--strong)' }}
          role="group"
          aria-label="Filter by state"
        >
          {(['open', 'all'] as StateFilter[]).map((o) => {
            const active = stateFilter === o;
            return (
              <button
                key={o}
                type="button"
                onClick={() => patch({ state: o === 'open' ? null : o })}
                className="h-8 px-3 t-caption cursor-pointer transition-colors capitalize"
                style={{
                  background: active ? 'var(--accent)' : 'transparent',
                  color: active ? 'var(--accent-fg)' : 'var(--fg-muted)',
                }}
              >
                {o}
              </button>
            );
          })}
        </div>
      </div>

      {/* Severity tallies mirror the dashboard card, and deep-link into the flat
          issue table pre-filtered when the operator wants the raw list. */}
      <div className="grid grid-cols-3 gap-2" style={{ maxWidth: 420 }}>
        {SEVS.map((sev) => (
          <Card key={sev} pad="sm" className="flex flex-col gap-1">
            <SeverityPill severity={sev} />
            <span className="t-metric tnum" style={{ color: 'var(--fg)' }}>
              {counts[sev]}
            </span>
          </Card>
        ))}
      </div>

      {error ? (
        <EmptyState
          variant="no-data"
          title="Could not load incidents"
          description="The daemon may still be starting, or the API is unreachable."
        />
      ) : loading && !data ? (
        <div className="flex flex-col gap-2 pt-2">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState variant="healthy" title="No open incidents" />
      ) : (
        <Card pad="md">
          <ul className="flex flex-col">
            {sorted.map((inc) => (
              <IncidentRow key={inc.id} incident={inc} now={now} />
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
