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
import {
  FAIL_MINUTE_DEFINITION,
  OFFENDER_BURDEN_DEFINITION,
  formatImpactMinutes,
  offenderClientMinutesNote,
  offenderDownMinutesNote,
  windowPhrase,
} from '../shared/format';

/**
 * Offenders (`/offenders`) — the "who causes most of my grief" leaderboard (§17).
 * Entities ranked by a composite burden: failed SLE client-minutes attributed to
 * them, open issues weighted by severity, and disconnect/roam churn over a window.
 * A sortable table; a Devices / Clients toggle switches surface. Read-only; the
 * ranking is store GROUP BYs.
 *
 * Two units, two columns, no sum (Gitea #38). "Client-minutes" is time real
 * clients spent below a service level because of this entity, and it is the only
 * SLE quantity the rank is built from. "Downtime" is the device's own offline
 * time, shown beside the score and never inside it — because a downed AP's harm
 * is already counted on the client axis (its clients moved to the next AP and
 * burned coverage minutes *there*), so scoring the downtime as well charges one
 * outage twice. Downtime accumulates easily and says nothing about how many
 * clients noticed, which is exactly how a loud harmless AP would come to outrank
 * a quiet costly one; the ordering property is pinned by a backend test.
 *
 * Neither column appears on the Clients surface: nothing is ever attributed *to*
 * a client (so client-minutes is structurally 0 there, and a rendered 0 would
 * read as "this client lost nothing", which is false — its lost minutes are
 * attributed to its AP), and a client has no state timeline to be down on.
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
  const clientsInWindow = data?.clients_in_window ?? null;

  const columns: Column<OffenderRow>[] = useMemo(() => {
    const cols: Column<OffenderRow>[] = [
      {
        key: 'entity',
        header: surface === 'devices' ? 'Device' : 'Client',
        sortAccessor: (r) => entityLabel(r.entity).toLowerCase(),
        render: (r) => <EntityLink entity={r.entity} />,
      },
      {
        key: 'score',
        header: <span title={OFFENDER_BURDEN_DEFINITION}>Burden score</span>,
        numeric: true,
        width: 128,
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
        width: 112,
        sortAccessor: (r) => r.issue_counts.total,
        render: (r) => <IssueBreakdown counts={r.issue_counts} />,
      },
    ];

    if (surface === 'devices') {
      cols.push(
        {
          key: 'fail_minutes',
          header: (
            <span title={offenderClientMinutesNote(clientsInWindow, windowS)}>Client-minutes</span>
          ),
          numeric: true,
          width: 132,
          sortAccessor: (r) => r.fail_minutes,
          render: (r) => (
            <span
              className="tnum"
              style={{ color: r.fail_minutes > 0 ? 'var(--fg)' : 'var(--fg-subtle)' }}
              title={FAIL_MINUTE_DEFINITION}
            >
              {formatImpactMinutes(r.fail_minutes)}
            </span>
          ),
        },
        {
          key: 'down_minutes',
          // The unit lives in the header, not in every cell (DataTable's rule).
          header: <span title={offenderDownMinutesNote(windowS)}>Downtime (min)</span>,
          numeric: true,
          width: 128,
          // nulls sink in both sort directions (DataTable), which is what an
          // unmeasured figure deserves: it never claims a rank it did not earn.
          sortAccessor: (r) => r.down_minutes,
          render: (r) => <DownMinutesCell minutes={r.down_minutes} windowS={windowS} />,
        },
      );
    }

    cols.push({
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
    });
    return cols;
  }, [surface, windowS, clientsInWindow]);

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-4" style={{ maxWidth: 1000 }}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            Top offenders
          </h2>
          <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
            {surface === 'devices'
              ? 'Ranked by the minutes clients lost because of them, their severity-weighted open issues, and disconnect/roam churn. A device’s own downtime is shown, never ranked on.'
              : 'Ranked by their disconnect/roam churn and their own open issues. A client’s lost minutes are attributed to the AP that caused them, so they rank devices, not clients.'}
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
          {rows.length} ranked
          {/* The denominator, published where the figures are read rather than
              only in a tooltip: a client-minute total is unreadable until you
              know how many clients were being watched. */}
          {surface === 'devices' && data.clients_in_window > 0 && (
            <>
              {' · '}
              {data.clients_in_window === 1
                ? '1 client judged'
                : `${data.clients_in_window} clients judged`}
              {` in the last ${windowPhrase(windowS)}`}
            </>
          )}
          {' · '}
          <RelativeTime ts={data.end_ts} mode="as-of" />
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

/**
 * The device's own offline time, in its own unit, in its own column.
 *
 * `null` is not `0`: null means nothing judged this device's state timeline over
 * the window, and rendering that as a zero would let an unwatched outage read as
 * a device that never went down. So null renders an em dash whose reason rides
 * along as hover text *and* as screen-reader text, and a measured zero renders a
 * quiet zero. `relative` is load-bearing — sr-only is absolutely positioned, and
 * without a positioned ancestor it resolves against the page, escaping the
 * table's horizontal scroller and dragging the body's scroll width with it.
 */
function DownMinutesCell({ minutes, windowS }: { minutes: number | null; windowS: number }) {
  if (minutes == null) {
    const note = `Downtime was not measured for this entity in the last ${windowPhrase(windowS)}, which is not the same as it staying up.`;
    return (
      <span className="relative" style={{ color: 'var(--fg-subtle)' }} title={note}>
        <span aria-hidden>—</span>
        <span className="sr-only">{note}</span>
      </span>
    );
  }
  if (minutes <= 0) {
    const note = `Measured: this device never went offline in the last ${windowPhrase(windowS)}.`;
    return (
      <span className="relative tnum" style={{ color: 'var(--fg-subtle)' }} title={note}>
        <span aria-hidden>0</span>
        <span className="sr-only">{note}</span>
      </span>
    );
  }
  const note = `Offline for ${formatImpactMinutes(minutes)} minutes in the last ${windowPhrase(windowS)}, from this device's own state timeline. Not part of the burden score, and never added to client-minutes.`;
  return (
    <span className="relative tnum" style={{ color: 'var(--fg)' }} title={note}>
      <span aria-hidden>{formatImpactMinutes(minutes)}</span>
      <span className="sr-only">{note}</span>
    </span>
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
