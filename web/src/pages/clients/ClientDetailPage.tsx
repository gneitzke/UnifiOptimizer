import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAsync } from '../../api';
import {
  Card,
  EmptyState,
  Skeleton,
  RelativeTime,
  fmt,
} from '../../components/ui';
import {
  getClient,
  getSle,
  type ClientDetail,
  type SleReport,
} from '../devices/api';
import { sampleMap } from '../devices/metrics';
import { MetricChart } from '../devices/MetricChart';
import { StateHistory } from '../devices/StateHistory';
import { IssueMiniList } from '../devices/IssueMiniList';
import { InfoRow, RangeToggle, RANGE_24H, SectionTitle } from '../devices/parts';
import { JourneyTimeline } from './JourneyTimeline';

/**
 * /clients/:id — one client in full (docs §Clients/:id). Journey timeline (AP
 * transitions, disconnects with reasons), RSSI + satisfaction history charts
 * with a 24h/7d toggle, the SLE fail-minutes this client is charged with, its
 * state history, and its issues past and present.
 */

const CLIENT_CHART_METRICS = ['rssi', 'satisfaction'];

function isWired(c: ClientDetail): boolean {
  return Boolean((c.meta as { is_wired?: boolean }).is_wired);
}

/** SLE windows where this client is charged fail-minutes, if any. */
function SleMinutes({
  report,
  entityId,
}: {
  report: SleReport | undefined;
  entityId: number;
}) {
  if (!report) return null;
  const rows: Array<{ sle: string; minutes: number }> = [];
  for (const [name, entry] of Object.entries(report.sles)) {
    for (const off of entry.top_offenders) {
      if (off.attributed_entity_id === entityId) {
        rows.push({ sle: name, minutes: off.fail_minutes });
      }
    }
  }
  if (rows.length === 0) {
    return (
      <p className="t-secondary" style={{ color: 'var(--fg-muted)' }}>
        Not a top offender in any service-level window — this client is not
        driving measured degradation.
      </p>
    );
  }
  rows.sort((a, b) => b.minutes - a.minutes);
  return (
    <div className="flex flex-col" style={{ marginTop: -4 }}>
      {rows.map((r) => (
        <InfoRow key={r.sle} label={r.sle[0].toUpperCase() + r.sle.slice(1)}>
          {fmt(r.minutes, 1)} fail min
        </InfoRow>
      ))}
    </div>
  );
}

export function ClientDetailPage() {
  const { id } = useParams<{ id: string }>();
  const entityId = Number(id);
  const { data, loading, error } = useAsync(() => getClient(entityId), [entityId]);
  const sleReq = useAsync(() => getSle(), []);
  const [range, setRange] = useState(RANGE_24H);

  const back = (
    <Link
      to="/clients"
      className="inline-flex items-center gap-1.5 t-secondary mb-4 hover:underline"
      style={{ color: 'var(--fg-muted)' }}
    >
      <ArrowLeft size={15} /> Clients
    </Link>
  );

  const wrap = (children: React.ReactNode) => (
    <div className="px-6 lg:px-8 py-6 max-w-[1100px] mx-auto">{children}</div>
  );

  if (loading && !data) {
    return wrap(
      <>
        {back}
        <Skeleton width={240} height={30} />
        <Card className="mt-4">
          <Skeleton width="100%" height={120} />
        </Card>
      </>,
    );
  }

  if (error) {
    return wrap(
      <>
        {back}
        <Card>
          <EmptyState
            variant="no-data"
            title={error.status === 404 ? 'Client not found' : 'Could not load client'}
            description={
              error.status === 404
                ? 'This client id is not in the inventory. It may have aged out.'
                : `The request failed (${error.status || 'network error'}).`
            }
          />
        </Card>
      </>,
    );
  }

  const client = data!;
  const m = sampleMap(client.metrics);
  const val = (k: string) => m.get(k)?.value ?? null;
  const wired = isWired(client);
  const present = new Set(client.metrics.map((s) => s.metric));
  const chartMetrics = CLIENT_CHART_METRICS.filter((k) => present.has(k));
  const connection = wired ? 'Wired' : (client.meta as { essid?: string }).essid || 'Wi‑Fi';

  return wrap(
    <>
      {back}

      <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
        <div className="min-w-0">
          <h2 className="t-page-title" style={{ color: 'var(--fg)' }}>
            {client.name}
          </h2>
          <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
            {connection}
            {client.current_ap ? (
              <>
                {' · '}
                <Link
                  to={`/devices/${client.current_ap.entity_id}`}
                  className="hover:underline"
                  style={{ color: 'var(--accent)' }}
                >
                  {client.current_ap.name}
                </Link>
              </>
            ) : null}
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Left rail: overview, SLE minutes, state history */}
        <div className="flex flex-col gap-4">
          <Card>
            <SectionTitle>Overview</SectionTitle>
            <div className="flex flex-col" style={{ marginTop: -4 }}>
              {!wired && (
                <>
                  <InfoRow label="Signal">
                    {val('rssi') == null ? '—' : `${fmt(val('rssi'), 0)} dBm`}
                  </InfoRow>
                  <InfoRow label="Noise">
                    {val('noise') == null ? '—' : `${fmt(val('noise'), 0)} dBm`}
                  </InfoRow>
                </>
              )}
              <InfoRow label="Satisfaction">
                {val('satisfaction') == null ? '—' : `${fmt(val('satisfaction'), 0)}%`}
              </InfoRow>
              <InfoRow label="Roams">
                {val('roam_count') == null ? '—' : fmt(val('roam_count'), 0)}
              </InfoRow>
              {client.state?.ip && (
                <InfoRow label="IP">
                  <span className="font-mono t-secondary">{client.state.ip}</span>
                </InfoRow>
              )}
              <InfoRow label="MAC">
                <span className="font-mono t-secondary">{client.native_id}</span>
              </InfoRow>
              <InfoRow label="First seen">
                <RelativeTime ts={client.first_seen_ts} mode="relative" />
              </InfoRow>
              <InfoRow label="Last update">
                <RelativeTime ts={client.last_seen_ts} mode="relative" />
              </InfoRow>
            </div>
          </Card>

          <Card>
            <SectionTitle>Service-level impact</SectionTitle>
            <SleMinutes report={sleReq.data} entityId={client.entity_id} />
          </Card>

          {client.state_changes.length > 0 && (
            <Card>
              <SectionTitle>State history</SectionTitle>
              <StateHistory changes={client.state_changes} />
            </Card>
          )}
        </div>

        {/* Main column: charts + journey */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          {chartMetrics.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-3">
                <h3 className="t-section" style={{ color: 'var(--fg)' }}>
                  History
                </h3>
                <RangeToggle value={range} onChange={setRange} />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                {chartMetrics.map((metric) => (
                  <MetricChart
                    key={metric}
                    entityId={client.entity_id}
                    metric={metric}
                    seconds={range}
                  />
                ))}
              </div>
            </section>
          )}

          <section>
            <Card>
              <SectionTitle>Journey</SectionTitle>
              <JourneyTimeline events={client.journey} />
            </Card>
          </section>
        </div>
      </div>

      <section className="mt-4">
        <Card>
          <SectionTitle>Issues</SectionTitle>
          <IssueMiniList open={client.issues_open} resolved={client.issues_resolved} />
        </Card>
      </section>
    </>,
  );
}
