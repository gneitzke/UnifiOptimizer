import type { HealthModel, SleScore } from '../model';
import { TimeSeriesChart, type Series } from '../../../components/ui/TimeSeriesChart';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { CategoryBars, type CategoryBar } from '../charts/CategoryBars';
import { BAND_COLOR, BAND_WORD } from '../severity';

/**
 * Section 6 — health & performance (docs/REPORT_SPEC.md §Health & performance):
 * a health trend line over the window, the per-SLE score bars, and per-SLE tiles
 * carrying each service level's band and its top offenders.
 */
export function HealthSection({
  health,
  site,
}: {
  health: HealthModel;
  site: string;
}) {
  const trendPoints = health.trend.points;
  const hasTrend = trendPoints.some((p) => p.value != null && Number.isFinite(p.value));
  const series: Series[] = [
    { name: 'Network health', points: trendPoints, kind: 'line', fill: true },
  ];

  const sleBars: CategoryBar[] = health.sles.map((s) => ({
    label: s.label,
    value: s.score,
    color: BAND_COLOR[s.band],
  }));

  return (
    <Section
      index={5}
      title="Health & performance"
      site={site}
      lead="Service-level scores over the window. Each score is the share of exposed minutes that met its target; lower means users felt it."
    >
      <div className="flex flex-col gap-6">
        <div
          className="rounded-card p-4"
          style={{ border: '1px solid var(--hairline)' }}
        >
          {hasTrend ? (
            <TimeSeriesChart
              series={series}
              percentage
              height={200}
              contextLabel="Overall network health"
              summaryStat={health.trend.summary_stat ?? undefined}
              asOf={health.trend.as_of}
              takeaway="0–100 score across the window; dips mark windows where service levels missed target."
            />
          ) : (
            <NoData label="No health trend was scored for this window." reason="Scores appear once the daemon has observed active clients for long enough to expose minutes." />
          )}
        </div>

        {health.sles.length > 0 && (
          <div
            className="rounded-card p-4"
            style={{ border: '1px solid var(--hairline)' }}
          >
            <CategoryBars
              data={sleBars}
              percentage
              contextLabel="Service-level scores"
              showValues
              height={180}
              takeaway="Each bar is the share of exposed minutes that met its target; the lower the bar, the more users felt it."
            />
          </div>
        )}

        <div>
          <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
            Service levels & top offenders
          </h3>
          {health.sles.length === 0 ? (
            <NoData label="No service levels were scored." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {health.sles.map((s) => (
                <SleTile key={s.key} sle={s} />
              ))}
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}

function SleTile({ sle }: { sle: SleScore }) {
  const offenders = sle.top_offenders.filter((o) => o.fail_minutes > 0).slice(0, 3);
  // A perfect score over an under-observed window is unproven, not clean: say so.
  const underObserved = sle.low_confidence && sle.score === 100;
  return (
    <div className="report-keep rounded-card p-3 flex flex-col gap-1.5" style={{ border: '1px solid var(--hairline)' }}>
      <div className="flex items-baseline justify-between">
        <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
          {sle.label}
        </span>
        <span className="inline-flex items-center gap-1.5 t-caption" style={{ color: 'var(--fg-muted)' }}>
          <span aria-hidden className="inline-block w-2 h-2 rounded-full" style={{ background: BAND_COLOR[sle.band] }} />
          {BAND_WORD[sle.band]}
        </span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="t-metric" style={{ color: 'var(--fg)' }}>
          {sle.score == null ? '—' : sle.score}
        </span>
        {sle.score != null && (
          <span className="t-secondary" style={{ color: 'var(--fg-subtle)' }}>
            / 100
          </span>
        )}
      </div>
      {sle.score == null ? (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          No exposed minutes
        </span>
      ) : offenders.length > 0 ? (
        <ul className="flex flex-col gap-0.5" style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {offenders.map((o, i) => (
            <li key={i} className="flex items-baseline justify-between gap-2 t-caption">
              <span style={{ color: 'var(--fg-muted)' }}>{o.name}</span>
              <span className="tnum" style={{ color: 'var(--fg-subtle)' }}>
                {o.fail_minutes} fail-min
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
          {underObserved ? 'No failed minutes, but few observed' : 'No failed minutes'}
        </span>
      )}
      {underObserved && (
        <span className="t-caption" style={{ color: 'var(--health-poor)' }}>
          Under-observed window — treat as indicative
        </span>
      )}
    </div>
  );
}
