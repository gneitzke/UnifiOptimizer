import type { ExecutiveModel } from '../model';
import { Section } from '../components/Section';
import { Scorecard } from '../components/Scorecard';
import { SeverityChip } from '../components/SeverityChip';
import { NoData } from '../components/NoData';

/**
 * Section 2 — the executive summary, sized to one page: a posture verdict, the
 * scorecard, the top findings in business language, and a one-line recommendation
 * summary (docs/REPORT_SPEC.md §Executive summary). No jargon, no dBm.
 */
export function ExecutiveSummary({
  exec,
  site,
}: {
  exec: ExecutiveModel;
  site: string;
}) {
  return (
    <Section
      index={1}
      title="Executive summary"
      site={site}
      lead="A plain-language read on how the network is serving its users, and where attention pays off first."
    >
      <div className="flex flex-col gap-6">
        {exec.verdict && (
          <p className="t-body" style={{ color: 'var(--fg)', fontSize: 16, lineHeight: '24px', maxWidth: '44rem' }}>
            {exec.verdict}
          </p>
        )}

        <Scorecard
          score={exec.overall_score}
          band={exec.band}
          counts={exec.severity_counts}
          lowConfidence={exec.low_confidence}
          confidenceNote={exec.confidence_note}
        />

        <div>
          <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
            What matters most
          </h3>
          {exec.top_findings.length === 0 ? (
            <NoData label="No findings were raised in this window." reason="Nothing in the assessment rose above the reporting threshold." />
          ) : (
            <ol className="flex flex-col gap-2.5" style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {exec.top_findings.map((f) => (
                <li
                  key={f.id}
                  className="report-keep flex gap-3 items-start rounded-card p-3"
                  style={{ border: '1px solid var(--hairline)' }}
                >
                  <SeverityChip severity={f.severity} glyphOnly className="mt-1" />
                  <div className="min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="t-body" style={{ color: 'var(--fg)', fontWeight: 500 }}>
                        {f.title}
                      </span>
                      <span className="t-caption tnum" style={{ color: 'var(--fg-subtle)' }}>
                        {f.id}
                      </span>
                    </div>
                    <p className="t-secondary mt-0.5" style={{ color: 'var(--fg-muted)' }}>
                      {f.business_impact}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>

        {exec.recommendation_summary && (
          <div
            className="report-keep rounded-card p-4"
            style={{ background: 'var(--canvas)', border: '1px solid var(--hairline)' }}
          >
            <span className="t-label" style={{ color: 'var(--fg-muted)' }}>
              Where to start
            </span>
            <p className="t-body mt-1" style={{ color: 'var(--fg)' }}>
              {exec.recommendation_summary}
            </p>
          </div>
        )}
      </div>
    </Section>
  );
}
