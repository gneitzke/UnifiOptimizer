import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { EmptyState } from '../../components/ui/EmptyState';
import { Skeleton } from '../../components/ui/Skeleton';
import { EntityLink } from '../shared/EntityLink';
import { listDeviceOffenders, type OffenderRow } from '../shared/api';
import { usePageAsync } from '../shared/hooks';
import {
  DOWN_MINUTE_DEFINITION,
  FAIL_MINUTE_DEFINITION,
  OFFENDER_BURDEN_DEFINITION,
  formatImpactMinutes,
  offenderClientMinutesNote,
} from '../shared/format';

/**
 * Top offenders (dashboard). The "who causes most of my grief" leaderboard
 * (§17): entities ranked by a composite burden — failed SLE client-minutes
 * attributed to them, open issues weighted by severity, disconnect/roam churn.
 * A quiet ranked list with a proportional burden bar (never a gauge or donut —
 * DESIGN_FOUNDATION rule 5); the full sortable table lives at /offenders.
 *
 * The chip under each name lists the channels that produced the rank, plus
 * downtime as a *separate* chip that produced none of it (Gitea #38): a downed
 * AP's harm already lands on the client axis, attributed to whichever AP its
 * clients moved to, so ranking on the downtime as well would charge one outage
 * twice and let a loud harmless device outrank a quiet costly one.
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
  const clientsInWindow = data?.clients_in_window ?? null;

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
            <OffenderRowItem
              key={o.entity_id}
              offender={o}
              rank={i + 1}
              max={max}
              clientsInWindow={clientsInWindow}
            />
          ))}
        </ol>
      )}
    </Card>
  );
}

interface BurdenPart {
  key: string;
  text: string;
  /** Reachable definition for a term that isn't self-explanatory (fail-min). */
  title?: string;
}

function burdenParts(o: OffenderRow, clientsInWindow: number | null): BurdenPart[] {
  const parts: BurdenPart[] = [];
  if (o.issue_counts.total > 0) {
    parts.push({
      key: 'issues',
      text: `${o.issue_counts.total} open issue${o.issue_counts.total === 1 ? '' : 's'}`,
    });
  }
  if (o.fail_minutes > 0) {
    // "client-min", not "fail-min": the number is now client-axis only, so it
    // can finally say whose minutes they were. The tooltip publishes the
    // denominator and window that the two words alone cannot.
    parts.push({
      key: 'client-min',
      text: `${formatImpactMinutes(o.fail_minutes)} client-min`,
      title: `${offenderClientMinutesNote(clientsInWindow, WINDOW_S)} ${FAIL_MINUTE_DEFINITION}`,
    });
  }
  if (o.event_count > 0) {
    parts.push({ key: 'events', text: `${o.event_count} disconnect/roam` });
  }
  // Downtime last, and last for a reason: it explains none of the rank above it.
  // Absent when null (not measured — never rendered as a zero) and when zero,
  // which the table at /offenders states explicitly and this preview has no room
  // to.
  if (o.down_minutes != null && o.down_minutes > 0) {
    parts.push({
      key: 'down-min',
      text: `down ${formatImpactMinutes(o.down_minutes)} min`,
      title: `${DOWN_MINUTE_DEFINITION} Not part of the burden score.`,
    });
  }
  return parts;
}

function OffenderRowItem({
  offender,
  rank,
  max,
  clientsInWindow,
}: {
  offender: OffenderRow;
  rank: number;
  max: number;
  clientsInWindow: number | null;
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
          <span
            className="t-caption tnum shrink-0"
            style={{ color: 'var(--fg-muted)' }}
            title={OFFENDER_BURDEN_DEFINITION}
          >
            <span style={{ color: 'var(--fg-subtle)' }}>Burden</span> {Math.round(offender.score)}
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
          {(() => {
            const parts = burdenParts(offender, clientsInWindow);
            if (parts.length === 0) return 'burden below threshold';
            return parts.map((p, i) => (
              <span key={p.key}>
                {i > 0 && ' · '}
                <span title={p.title}>{p.text}</span>
              </span>
            ));
          })()}
        </span>
      </div>
    </li>
  );
}
