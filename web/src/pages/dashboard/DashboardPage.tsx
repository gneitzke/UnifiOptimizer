import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { Card } from '../../components/ui/Card';
import { Skeleton } from '../../components/ui/Skeleton';
import { RelativeTime } from '../../components/ui/RelativeTime';
import { useHealth } from '../../api/hooks';
import { useWsFrames } from '../../api/WsProvider';
import { getSle } from '../shared/api';
import { usePageAsync } from '../shared/hooks';
import { scoreBand, scoreTo100 } from '../shared/format';
import { SleHealthBlock } from './SleHealthBlock';
import { CollectorStrip } from './CollectorStrip';
import { FirstRunNotice } from './FirstRunNotice';
import { IncidentsSummary } from './IncidentsSummary';
import { TopOffenders } from './TopOffenders';
import { EventTicker, type TickerTransition } from './EventTicker';
import { REPORT_ROUTE } from '../report/exportReport';

/**
 * Dashboard (`/`): SLE health blocks with inline classifier breakdowns, a fixed
 * 0-100 24h trend per SLE, active-issues-by-severity, a live activity ticker fed
 * by the WebSocket, and a collector-health strip. One WebSocket subscription for
 * the whole page: issue transitions both nudge the issue summary to refetch and
 * stream into the ticker.
 */

const SLE_ORDER = ['coverage', 'capacity', 'connect', 'roaming', 'wan', 'infra'];
const SLE_WINDOW_S = 86_400;
const SLE_BUCKETS = 48;

const BAND_COLOR: Record<ReturnType<typeof scoreBand>, string> = {
  good: 'var(--sev-healthy)',
  fair: 'var(--sev-p3)',
  poor: 'var(--sev-p2)',
  none: 'var(--fg-subtle)',
};
const BAND_WORD: Record<ReturnType<typeof scoreBand>, string> = {
  good: 'Healthy',
  fair: 'Fair',
  poor: 'Degraded',
  none: 'No data',
};

export function DashboardPage() {
  const navigate = useNavigate();
  const sle = usePageAsync(() => getSle(SLE_WINDOW_S, SLE_BUCKETS), [], {
    pollMs: 60_000,
  });
  const health = useHealth(30_000);

  const [transitions, setTransitions] = useState<TickerTransition[]>([]);
  const [issueNonce, setIssueNonce] = useState(0);

  useWsFrames((frame) => {
    if (frame.type !== 'issue_transition') return;
    const stamped: TickerTransition = {
      ...frame,
      ts: typeof frame.ts === 'number' ? frame.ts : Math.floor(Date.now() / 1000),
    };
    setTransitions((prev) => [stamped, ...prev].slice(0, 40));
    setIssueNonce((n) => n + 1);
  });

  const report = sle.data;
  const headline100 = scoreTo100(report?.headline);
  const band = scoreBand(headline100);
  const scoredCount = report ? Object.values(report.sles).filter((s) => s.score != null).length : 0;
  const totalSle = report ? Object.keys(report.sles).length : 0;

  const orderedSles = report
    ? [
        ...SLE_ORDER.filter((k) => report.sles[k]),
        ...Object.keys(report.sles).filter((k) => !SLE_ORDER.includes(k)),
      ]
    : [];

  // First-run / freshly-connected: the daemon is reachable but no service level
  // has scored yet. A brand-new connect has no history, so the panels are honestly
  // empty — say "collecting now" rather than render a broken-looking blank. Only
  // while there is genuinely nothing scored, and only once something has loaded
  // (never during the initial skeleton, and never masking a real load error).
  const collecting =
    !sle.error && !health.error && scoredCount === 0 && (report != null || health.data != null);

  return (
    <div className="px-6 py-6 mx-auto flex flex-col gap-6" style={{ maxWidth: 1200 }}>
      <div className="flex justify-end">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => navigate(REPORT_ROUTE)}
        >
          <FileText size={15} aria-hidden />
          Export report
        </Button>
      </div>

      {collecting && <FirstRunNotice backfill={health.data?.backfill} />}

      {/* Headline + collectors */}
      <section className="grid gap-4 lg:grid-cols-[minmax(260px,1fr)_2fr] items-stretch">
        <Card data-tour="network-health" pad="md" className="flex flex-col justify-between gap-2">
          <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
            Network health
          </span>
          {sle.error ? (
            <span className="t-body" style={{ color: 'var(--fg-muted)' }}>
              Score unavailable
            </span>
          ) : sle.loading && !report ? (
            <Skeleton className="h-9 w-24" />
          ) : (
            <div className="flex items-baseline gap-2">
              <span className="t-metric" style={{ color: 'var(--fg)' }}>
                {headline100 == null ? '—' : headline100}
              </span>
              <span
                className="inline-flex items-center gap-1.5 t-secondary"
                style={{ color: 'var(--fg-muted)' }}
              >
                <span
                  aria-hidden
                  className="inline-block w-2.5 h-2.5 rounded-full"
                  style={{ background: BAND_COLOR[band] }}
                />
                {BAND_WORD[band]}
              </span>
            </div>
          )}
          {report && (
            <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
              Weighted across {scoredCount}/{totalSle} service levels ·{' '}
              <RelativeTime ts={report.end_ts} mode="as-of" />
            </span>
          )}
        </Card>

        <CollectorStrip health={health.data} loading={health.loading} error={!!health.error} />
      </section>

      {/* Service levels */}
      <section className="flex flex-col gap-3">
        <h2 className="t-section" style={{ color: 'var(--fg)' }}>
          Service levels
        </h2>
        {sle.error ? (
          <Card pad="md">
            <span className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
              The SLE report could not be loaded. The daemon may still be starting.
            </span>
          </Card>
        ) : sle.loading && !report ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {SLE_ORDER.map((k) => (
              <Card key={k} pad="sm" className="flex flex-col gap-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-8 w-16" />
                <Skeleton className="h-[92px] w-full" />
              </Card>
            ))}
          </div>
        ) : report && orderedSles.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {orderedSles.map((key) => (
              <SleHealthBlock
                key={key}
                sleKey={key}
                entry={report.sles[key]}
                startTs={report.start_ts}
                endTs={report.end_ts}
                buckets={SLE_BUCKETS}
              />
            ))}
          </div>
        ) : (
          <Card pad="md">
            <span className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
              No service-level minutes collected yet. Scores appear within a few minutes of
              connecting, once the daemon has observed active clients.
            </span>
          </Card>
        )}
      </section>

      {/* Incidents + activity */}
      <section className="grid gap-4 lg:grid-cols-[2fr_3fr] items-start">
        <IncidentsSummary reloadKey={issueNonce} />
        <EventTicker transitions={transitions} />
      </section>

      {/* Top offenders */}
      <section className="grid gap-4 lg:grid-cols-[2fr_3fr] items-start">
        <TopOffenders />
      </section>
    </div>
  );
}
