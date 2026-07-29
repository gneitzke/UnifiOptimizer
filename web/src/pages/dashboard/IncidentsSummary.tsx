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
 * "Needs attention" (dashboard). Every piece of open work, honestly labeled
 * (Gitea #21): a genuine incident (2+ members) shows its root-cause line and a
 * "+N related" expander that reveals the symptoms; a standalone issue is a
 * one-member incident-of-one that links straight to the issue. This card
 * intentionally uses the engine's uniform projection
 * (`include_singletons=true`) rather than the genuine-only default — its job
 * is triage across everything open, not to lead with "incidents" as a concept
 * (that is the Issues page's job now). A clean network gets the honest
 * one-line positive, never a fabricated all-clear.
 */

const SEVS: Severity[] = ['p1', 'p2', 'p3'];
const PREVIEW = 6;

export function IncidentsSummary({ reloadKey }: { reloadKey: number }) {
  const { data, loading, error } = usePageAsync(() => listIncidents(false, true), [reloadKey], {
    pollMs: 30_000,
  });
  const now = useNowSeconds();

  const incidents = data?.incidents ?? [];
  const suppressedExcluded = data?.suppressed_excluded ?? 0;
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
          Needs attention
        </h2>
        <Link
          to="/issues"
          className="inline-flex items-center gap-0.5 t-caption hover:underline"
          style={{ color: 'var(--accent)' }}
        >
          All issues
          <ChevronRight size={13} />
        </Link>
      </div>

      {error ? (
        <div className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          Could not load open issues.
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
                    to="/issues"
                    className="t-caption hover:underline"
                    style={{ color: 'var(--accent)' }}
                  >
                    +{incidents.length - top.length} more
                  </Link>
                </li>
              )}
            </ul>
          )}
          {/* Disclosure, not decoration: a fully-suppressed incident drops out of
              this card, so it must say how many, or the count reads as a silent
              improvement. Mirrors IssueSeveritySummary's "N suppressed" caption. */}
          {suppressedExcluded > 0 && (
            <Link
              to="/issues?state=suppressed"
              className="t-caption hover:underline"
              style={{ color: 'var(--fg-muted)' }}
            >
              {suppressedExcluded} suppressed, not shown
            </Link>
          )}
        </>
      )}
    </Card>
  );
}
