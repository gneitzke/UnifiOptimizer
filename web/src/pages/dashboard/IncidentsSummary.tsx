import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { severityRank } from '../shared/format';
import { listIncidents } from '../shared/api';
import { usePageAsync, useNowSeconds } from '../shared/hooks';
import { IncidentRow } from '../incidents/IncidentRow';
import type { Severity } from '../../api/types';

/**
 * Active incidents (dashboard). The section §17 says the dashboard leads with:
 * each card is an INCIDENT (one root cause + its symptoms), not a scatter of
 * issues. A multi-member incident shows its root-cause line and a "+N related"
 * expander that reveals the symptoms; a standalone issue is a one-member
 * incident-of-one that links straight to the issue. A clean network gets the
 * honest one-line positive, never a fabricated all-clear.
 */

const SEVS: Severity[] = ['p1', 'p2', 'p3'];
const PREVIEW = 6;

export function IncidentsSummary({ reloadKey }: { reloadKey: number }) {
  const { data, loading, error } = usePageAsync(() => listIncidents(), [reloadKey], {
    pollMs: 30_000,
  });
  const now = useNowSeconds();

  const incidents = data?.incidents ?? [];
  const counts: Record<Severity, number> = { p1: 0, p2: 0, p3: 0 };
  for (const inc of incidents) counts[inc.severity] += 1;
  const sorted = [...incidents].sort((a, b) => {
    const s = severityRank(a.severity) - severityRank(b.severity);
    if (s !== 0) return s;
    return a.first_seen_ts - b.first_seen_ts; // longest-running first
  });
  const top = sorted.slice(0, PREVIEW);

  return (
    <Card pad="md" className="flex flex-col gap-3 min-w-0">
      <div className="flex items-baseline justify-between">
        <h2 className="t-section" style={{ color: 'var(--fg)' }}>
          Active incidents
        </h2>
        <Link
          to="/incidents"
          className="inline-flex items-center gap-0.5 t-caption hover:underline"
          style={{ color: 'var(--accent)' }}
        >
          All incidents
          <ChevronRight size={13} />
        </Link>
      </div>

      {error ? (
        <div className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          Could not load incidents.
        </div>
      ) : loading && !data ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            {SEVS.map((sev) => (
              <Link
                key={sev}
                to={`/issues?severity=${sev}&state=active`}
                className="flex flex-col gap-1 px-3 py-2 rounded-control transition-colors hover:bg-canvas"
                style={{ border: '1px solid var(--hairline)' }}
              >
                <SeverityPill severity={sev} />
                <span className="t-metric tnum" style={{ color: 'var(--fg)' }}>
                  {counts[sev]}
                </span>
              </Link>
            ))}
          </div>

          {incidents.length === 0 ? (
            <EmptyState variant="healthy" />
          ) : (
            <ul className="flex flex-col">
              {top.map((inc) => (
                <IncidentRow key={inc.id} incident={inc} now={now} />
              ))}
              {incidents.length > top.length && (
                <li className="pt-2">
                  <Link
                    to="/incidents"
                    className="t-caption hover:underline"
                    style={{ color: 'var(--accent)' }}
                  >
                    +{incidents.length - top.length} more incidents
                  </Link>
                </li>
              )}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}
