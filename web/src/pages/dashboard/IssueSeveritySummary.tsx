import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { StatePill } from '../../components/ui/StatePill';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { ongoingLabel, severityRank, issueDurationSeconds } from '../shared/format';
import { listIssues, entityLabel, type IssueRow } from '../shared/api';
import { usePageAsync, useNowSeconds } from '../shared/hooks';
import type { Severity } from '../../api/types';

/**
 * Active-issues-by-severity summary (dashboard). Counts open issues per severity
 * with quiet links into the filtered issues list, then previews the most severe
 * few. A genuinely-clean network gets the honest one-line "No active issues"
 * positive (docs §Interaction) — never a fabricated all-clear.
 */

const SEVS: Severity[] = ['p1', 'p2', 'p3'];

export function IssueSeveritySummary({ reloadKey }: { reloadKey: number }) {
  const { data, loading, error } = usePageAsync(() => listIssues(), [reloadKey], {
    pollMs: 30_000,
  });
  const now = useNowSeconds();

  const { counts, open, top } = useMemo(() => {
    const issues = data?.issues ?? [];
    const openIssues = issues.filter((i) => i.state !== 'resolved');
    const c: Record<Severity, number> = { p1: 0, p2: 0, p3: 0 };
    for (const i of openIssues) c[i.severity] += 1;
    const sorted = [...openIssues].sort((a, b) => {
      const s = severityRank(a.severity) - severityRank(b.severity);
      if (s !== 0) return s;
      return issueDurationSeconds(b, now) - issueDurationSeconds(a, now);
    });
    return { counts: c, open: openIssues, top: sorted.slice(0, 5) };
  }, [data, now]);

  return (
    <Card pad="md" className="flex flex-col gap-3 min-w-0">
      <div className="flex items-baseline justify-between">
        <h2 className="t-section" style={{ color: 'var(--fg)' }}>
          Active issues
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
          Could not load issues.
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

          {open.length === 0 ? (
            <EmptyState variant="healthy" />
          ) : (
            <ul className="flex flex-col">
              {top.map((issue) => (
                <TopIssueRow key={issue.id} issue={issue} now={now} />
              ))}
              {open.length > top.length && (
                <li className="pt-2">
                  <Link
                    to="/issues"
                    className="t-caption hover:underline"
                    style={{ color: 'var(--accent)' }}
                  >
                    +{open.length - top.length} more open
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

function TopIssueRow({ issue, now }: { issue: IssueRow; now: number }) {
  return (
    <li style={{ borderTop: '1px solid var(--hairline)' }}>
      <Link
        to={`/issues/${issue.id}`}
        className="flex items-center gap-2 py-2 transition-colors hover:bg-canvas -mx-1 px-1 rounded"
      >
        <SeverityPill severity={issue.severity} glyphOnly />
        <div className="flex flex-col min-w-0 flex-1">
          <span className="t-body truncate" style={{ color: 'var(--fg)' }}>
            {issue.title}
          </span>
          <span className="t-caption truncate" style={{ color: 'var(--fg-muted)' }}>
            {issue.entity ? entityLabel(issue.entity) : 'network-wide'}
            {' · '}
            {ongoingLabel(issue, now)}
          </span>
        </div>
        <StatePill state={issue.state} severity={issue.severity} />
      </Link>
    </li>
  );
}
