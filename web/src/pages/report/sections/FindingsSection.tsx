import type { EvidenceChart, Finding } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { DocTable, type DocColumn } from '../components/DocTable';
import { SeverityChip } from '../components/SeverityChip';
import { TimeSeriesChart, type Series } from '../../../components/ui/TimeSeriesChart';
import { CategoryBars, type CategoryBar } from '../charts/CategoryBars';
import { statusColor } from '../severity';

/**
 * Section 9 — detailed findings (docs/REPORT_SPEC.md §Detailed findings): a
 * summary table, then every finding in the identical fixed template — ID,
 * severity, affected assets, observation (+ its evidence), impact, root cause, and
 * an ordered, specific recommendation. Correlated symptoms arrive already grouped
 * under one finding by the backend incident engine.
 */
export function FindingsSection({
  findings,
  site,
}: {
  findings: Finding[];
  site: string;
}) {
  const summaryColumns: DocColumn<Finding>[] = [
    { key: 'id', header: 'ID', width: 88, render: (f) => <span className="tnum" style={{ color: 'var(--fg-muted)' }}>{f.id}</span> },
    { key: 'title', header: 'Finding', render: (f) => <span style={{ fontWeight: 500 }}>{f.title}</span> },
    { key: 'sev', header: 'Severity', width: 110, render: (f) => <SeverityChip severity={f.severity} /> },
    { key: 'affected', header: 'Affected', render: (f) => (f.affected.length ? f.affected.join(', ') : '—') },
  ];

  return (
    <Section
      index={8}
      title="Detailed findings"
      site={site}
      lead="Each finding states the measured fact, who it affects, the likely root cause, and a specific fix."
    >
      {findings.length === 0 ? (
        <NoData label="No findings were raised in this window." reason="Nothing crossed the reporting threshold. That is a result, not an omission." />
      ) : (
        <div className="flex flex-col gap-6">
          <div>
            <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
              Summary
            </h3>
            <DocTable columns={summaryColumns} rows={findings} rowKey={(f) => f.id} />
          </div>

          <div className="flex flex-col gap-5">
            {findings.map((f) => (
              <FindingEntry key={f.id} finding={f} />
            ))}
          </div>
        </div>
      )}
    </Section>
  );
}

function FindingEntry({ finding }: { finding: Finding }) {
  return (
    <article
      className="report-finding rounded-card overflow-hidden"
      style={{ border: '1px solid var(--hairline)' }}
    >
      <header
        className="flex items-start gap-3 p-4"
        style={{ borderBottom: '1px solid var(--hairline)', background: 'var(--canvas)' }}
      >
        <SeverityChip severity={finding.severity} glyphOnly className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="tnum t-caption" style={{ color: 'var(--fg-subtle)' }}>
              {finding.id}
            </span>
            <h4 className="t-section" style={{ color: 'var(--fg)', fontSize: 15 }}>
              {finding.title}
            </h4>
          </div>
        </div>
        <SeverityChip severity={finding.severity} />
      </header>

      <div className="p-4 flex flex-col gap-4">
        <Field label="Affected assets">
          {finding.affected.length ? finding.affected.join(', ') : '—'}
        </Field>

        <Field label="Observation">
          <p>{finding.observation}</p>
          {finding.evidence.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2">
              {finding.evidence.map((e, i) => (
                <span
                  key={i}
                  className="inline-flex items-baseline gap-1 px-2 py-0.5 rounded-control t-caption"
                  style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
                >
                  <span style={{ color: 'var(--fg-subtle)' }}>{e.label}</span>
                  <span className="tnum" style={{ color: 'var(--fg)' }}>
                    {e.value}
                  </span>
                </span>
              ))}
            </div>
          )}
          {finding.evidence_chart && (
            <div className="mt-3">
              <EvidenceChartView chart={finding.evidence_chart} />
            </div>
          )}
        </Field>

        <Field label="Impact">{finding.impact}</Field>
        <Field label="Root cause">{finding.root_cause}</Field>

        <Field label="Recommendation">
          {finding.recommendation.length === 0 ? (
            <span style={{ color: 'var(--fg-subtle)' }}>—</span>
          ) : finding.recommendation.length === 1 ? (
            <p>{finding.recommendation[0]}</p>
          ) : (
            <ol className="flex flex-col gap-1.5" style={{ margin: 0, paddingLeft: 18 }}>
              {finding.recommendation.map((r, i) => (
                <li key={i} className="tnum" style={{ color: 'var(--fg)' }}>
                  <span className="t-body">{r}</span>
                </li>
              ))}
            </ol>
          )}
        </Field>
      </div>
    </article>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div
      className="grid gap-x-4 gap-y-1"
      style={{ gridTemplateColumns: 'minmax(120px, 132px) 1fr' }}
    >
      <div
        className="t-label"
        style={{ color: 'var(--fg-muted)', textTransform: 'uppercase', letterSpacing: '0.03em' }}
      >
        {label}
      </div>
      <div className="t-body min-w-0" style={{ color: 'var(--fg)', maxWidth: '42rem' }}>
        {children}
      </div>
    </div>
  );
}

function EvidenceChartView({ chart }: { chart: EvidenceChart }) {
  if (chart.kind === 'timeseries' && chart.points && chart.points.length > 0) {
    const series: Series[] = [
      { name: chart.context_label, points: chart.points, kind: 'line', fill: true },
    ];
    return (
      <TimeSeriesChart
        series={series}
        percentage={chart.percentage}
        zeroBaseline={chart.percentage}
        height={170}
        contextLabel={chart.context_label}
        summaryStat={chart.summary_stat ?? undefined}
        yUnit={chart.unit ?? undefined}
        reference={chart.reference ? { value: chart.reference.value, label: chart.reference.label ?? undefined } : undefined}
        takeaway={chart.takeaway ?? undefined}
      />
    );
  }
  if (chart.kind === 'bars' && chart.bars && chart.bars.length > 0) {
    const bars: CategoryBar[] = chart.bars.map((b) => ({
      label: b.label,
      value: b.value,
      color: statusColor(b.status),
    }));
    return (
      <CategoryBars
        data={bars}
        percentage={chart.percentage}
        reference={chart.reference ? { value: chart.reference.value, label: chart.reference.label ?? undefined } : undefined}
        unit={chart.unit ?? undefined}
        contextLabel={chart.context_label}
        summaryStat={chart.summary_stat ?? undefined}
        takeaway={chart.takeaway ?? undefined}
        height={170}
      />
    );
  }
  return null;
}
