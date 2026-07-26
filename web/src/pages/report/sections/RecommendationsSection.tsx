import type { Phase, Recommendation } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { SeverityChip } from '../components/SeverityChip';

/**
 * Section 10 — recommendations / roadmap (docs/REPORT_SPEC.md §Recommendations):
 * phased into now / soon / strategic, each item traceable to a finding ID and
 * specific enough to act on. Ordering within a phase is the backend's ranking.
 */

const PHASE_ORDER: Phase[] = ['now', 'soon', 'strategic'];
const PHASE_META: Record<Phase, { title: string; blurb: string }> = {
  now: { title: 'Now', blurb: 'Fixes that stop active user impact.' },
  soon: { title: 'Soon', blurb: 'Address within the next maintenance window.' },
  strategic: { title: 'Strategic', blurb: 'Plan into the next refresh or design change.' },
};

export function RecommendationsSection({
  recommendations,
  site,
}: {
  recommendations: Recommendation[];
  site: string;
}) {
  const byPhase = (p: Phase) => recommendations.filter((r) => r.phase === p);

  return (
    <Section
      index={9}
      title="Recommendations"
      site={site}
      lead="A phased plan. Each item points back to the finding it resolves."
    >
      {recommendations.length === 0 ? (
        <NoData label="No recommendations. Nothing crossed the reporting threshold." />
      ) : (
        <div className="flex flex-col gap-6">
          {PHASE_ORDER.map((phase) => {
            const items = byPhase(phase);
            if (items.length === 0) return null;
            const meta = PHASE_META[phase];
            return (
              <div key={phase}>
                <div className="flex items-baseline gap-3 mb-2">
                  <h3 className="t-section" style={{ color: 'var(--fg)' }}>
                    {meta.title}
                  </h3>
                  <span className="t-caption" style={{ color: 'var(--fg-subtle)' }}>
                    {meta.blurb}
                  </span>
                </div>
                <ol className="flex flex-col" style={{ margin: 0, padding: 0, listStyle: 'none', border: '1px solid var(--hairline)', borderRadius: 'var(--radius-card)', overflow: 'hidden' }}>
                  {items.map((r, i) => (
                    <li
                      key={`${r.finding_id}-${i}`}
                      className="flex items-start gap-3 px-4 py-3"
                      style={{ borderTop: i === 0 ? 'none' : '1px solid var(--hairline)' }}
                    >
                      <SeverityChip severity={r.severity} glyphOnly className="mt-0.5" />
                      <div className="min-w-0 flex-1">
                        <p className="t-body" style={{ color: 'var(--fg)', maxWidth: '44rem' }}>
                          {r.text}
                        </p>
                      </div>
                      <span className="tnum t-caption shrink-0" style={{ color: 'var(--fg-subtle)' }}>
                        {r.finding_id}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}
