import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { EntityLink } from '../shared/EntityLink';
import { listDeviceOffenders, type OffenderRow } from '../shared/api';
import { usePageAsync } from '../shared/hooks';

/**
 * Top offenders (dashboard). The "who causes most of my grief" leaderboard
 * (§17): entities ranked by a composite burden — failed SLE client-minutes
 * attributed to them, open issues weighted by severity, disconnect/roam churn.
 * A quiet ranked list with a proportional burden bar (never a gauge or donut —
 * DESIGN_FOUNDATION rule 5); the full sortable table lives at /offenders.
 */

const WINDOW_S = 86_400;
const PREVIEW = 5;

export function TopOffenders() {
  const { data, loading, error } = usePageAsync(
    () => listDeviceOffenders(WINDOW_S, PREVIEW),
    [],
    { pollMs: 60_000 },
  );

  const offenders = data?.offenders ?? [];
  const max = offenders.reduce((m, o) => Math.max(m, o.score), 0) || 1;

  return (
    <Card pad="md" className="flex flex-col gap-3 min-w-0">
      <div className="flex items-baseline justify-between">
        <h2 className="t-section" style={{ color: 'var(--fg)' }}>
          Top offenders
        </h2>
        <Link
          to="/offenders"
          className="inline-flex items-center gap-0.5 t-caption hover:underline"
          style={{ color: 'var(--accent)' }}
        >
          All offenders
          <ChevronRight size={13} />
        </Link>
      </div>

      {error ? (
        <div className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          Could not load offenders.
        </div>
      ) : loading && !data ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : offenders.length === 0 ? (
        <EmptyState variant="healthy" title="No problem devices" />
      ) : (
        <ol className="flex flex-col">
          {offenders.map((o, i) => (
            <OffenderRowItem key={o.entity_id} offender={o} rank={i + 1} max={max} />
          ))}
        </ol>
      )}
    </Card>
  );
}

function burdenLabel(o: OffenderRow): string {
  const parts: string[] = [];
  if (o.issue_counts.total > 0) parts.push(`${o.issue_counts.total} open issue${o.issue_counts.total === 1 ? '' : 's'}`);
  if (o.fail_minutes > 0) parts.push(`${Math.round(o.fail_minutes)} fail-min`);
  if (o.event_count > 0) parts.push(`${o.event_count} disconnect/roam`);
  return parts.join(' · ') || 'burden below threshold';
}

function OffenderRowItem({
  offender,
  rank,
  max,
}: {
  offender: OffenderRow;
  rank: number;
  max: number;
}) {
  const pct = Math.max(4, Math.round((offender.score / max) * 100));
  return (
    <li
      className="flex items-center gap-3 py-2"
      style={{ borderTop: rank === 1 ? undefined : '1px solid var(--hairline)' }}
    >
      <span className="t-caption tnum w-4 text-right" style={{ color: 'var(--fg-subtle)' }}>
        {rank}
      </span>
      <div className="flex flex-col min-w-0 flex-1 gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <EntityLink entity={offender.entity} className="truncate" />
          <span className="t-caption tnum" style={{ color: 'var(--fg-muted)' }}>
            {Math.round(offender.score)}
          </span>
        </div>
        <div
          className="h-1.5 rounded-full overflow-hidden"
          style={{ background: 'var(--hairline)' }}
          aria-hidden
        >
          <div
            className="h-full rounded-full"
            style={{ width: `${pct}%`, background: 'var(--accent)' }}
          />
        </div>
        <span className="t-micro truncate" style={{ color: 'var(--fg-subtle)' }}>
          {burdenLabel(offender)}
        </span>
      </div>
    </li>
  );
}
