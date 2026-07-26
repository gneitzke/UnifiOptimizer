import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Card } from '../../components/ui/Card';
import { SeverityPill } from '../../components/ui/SeverityPill';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { Skeleton } from '../../components/ui/Skeleton';
import { EntityLink } from '../shared/EntityLink';
import { listEvents, type NetEventRow } from '../shared/api';
import { usePageAsync } from '../shared/hooks';
import type { IssueTransitionFrame, Severity } from '../../api/types';

/**
 * Live activity ticker (dashboard). The genuinely-pushed channel is the
 * WebSocket's issue transitions (prepended the instant they arrive); recent
 * controller events come from `/api/events`, polled and honestly time-stamped —
 * we never present polled data as a live stream. The two are merged newest-first.
 */

export interface TickerTransition extends IssueTransitionFrame {
  /** Local receive time in epoch seconds (set by the shell when ts is absent). */
  ts: number;
}

type Item =
  | { kind: 'event'; ts: number; key: string; ev: NetEventRow }
  | { kind: 'transition'; ts: number; key: string; tr: TickerTransition };

const MAX_ITEMS = 40;

export function EventTicker({ transitions }: { transitions: TickerTransition[] }) {
  const { data, loading, error } = usePageAsync(() => listEvents({ limit: 30 }), [], {
    pollMs: 20_000,
  });

  const items = useMemo<Item[]>(() => {
    const evItems: Item[] = (data?.events ?? []).map((ev) => ({
      kind: 'event',
      ts: ev.ts,
      key: `e-${ev.id}`,
      ev,
    }));
    const trItems: Item[] = transitions.map((tr) => ({
      kind: 'transition',
      ts: tr.ts,
      key: `t-${tr.issue_id}-${tr.ts}`,
      tr,
    }));
    return [...trItems, ...evItems]
      .sort((a, b) => b.ts - a.ts)
      .slice(0, MAX_ITEMS);
  }, [data, transitions]);

  return (
    <Card pad="md" className="flex flex-col gap-3 min-w-0">
      <h2 className="t-section" style={{ color: 'var(--fg)' }}>
        Recent activity
      </h2>

      {error ? (
        <div className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
          Could not load recent events.
        </div>
      ) : loading && !data ? (
        <div className="flex flex-col gap-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="t-secondary py-2" style={{ color: 'var(--fg-subtle)' }}>
          No recent events.
        </div>
      ) : (
        <ul className="flex flex-col max-h-[420px] overflow-y-auto -mr-1 pr-1">
          {items.map((it) => (
            <li
              key={it.key}
              className="flex items-start gap-3 py-2"
              style={{ borderTop: '1px solid var(--hairline)' }}
            >
              <RelativeTime
                ts={it.ts}
                mode="relative"
                className="t-caption tnum shrink-0 w-14 pt-0.5"
              />
              {it.kind === 'transition' ? (
                <TransitionRow tr={it.tr} />
              ) : (
                <EventRow ev={it.ev} />
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function TransitionRow({ tr }: { tr: TickerTransition }) {
  // The frame carries its issue's real severity; when a frame predates that field
  // (or omits it) we render a NEUTRAL marker rather than defaulting to a concrete
  // P3 pill, which would misencode severity by color (never-do rules 1/2).
  const sev = tr.severity as Severity | undefined;
  return (
    <Link to={`/issues/${tr.issue_id}`} className="flex items-start gap-2 min-w-0 flex-1 group">
      {sev ? (
        <SeverityPill severity={sev} glyphOnly className="mt-0.5" />
      ) : (
        <span
          aria-hidden
          title="Issue update"
          className="inline-block w-2 h-2 rounded-full mt-1.5 shrink-0"
          style={{ background: 'var(--sev-neutral)' }}
        />
      )}
      <div className="flex flex-col min-w-0">
        <span className="t-body truncate group-hover:underline" style={{ color: 'var(--fg)' }}>
          {tr.title ?? `Issue #${tr.issue_id}`}
        </span>
        <span className="t-caption" style={{ color: 'var(--fg-muted)' }}>
          {tr.from_state ? `${tr.from_state} → ${tr.to_state ?? '?'}` : tr.to_state ?? 'updated'}
        </span>
      </div>
    </Link>
  );
}

function EventRow({ ev }: { ev: NetEventRow }) {
  return (
    <div className="flex flex-col min-w-0 flex-1">
      <span className="t-body truncate" style={{ color: 'var(--fg)' }}>
        {ev.msg || ev.key}
      </span>
      <span className="t-caption truncate flex items-center gap-1" style={{ color: 'var(--fg-muted)' }}>
        <code className="t-micro" style={{ color: 'var(--fg-subtle)' }}>
          {ev.key}
        </code>
        {ev.entity && (
          <>
            <span aria-hidden>·</span>
            <EntityLink entity={ev.entity} muted />
          </>
        )}
      </span>
    </div>
  );
}
