import type { AppendixModel } from '../model';
import { Section } from '../components/Section';
import { NoData } from '../components/NoData';
import { SeverityChip } from '../components/SeverityChip';
import { DocTable, type DocColumn } from '../components/DocTable';
import type { ThresholdRow } from '../model';

/**
 * Section 11 — appendix (docs/REPORT_SPEC.md §Appendix): the severity rubric, the
 * thresholds the assessment used, a glossary, and the methodology detail. This is
 * what makes a number auditable — every threshold cited, every term defined.
 */
export function AppendixSection({
  appendix,
  site,
}: {
  appendix: AppendixModel;
  site: string;
}) {
  const thresholdColumns: DocColumn<ThresholdRow>[] = [
    { key: 'name', header: 'Threshold', render: (t) => <span style={{ fontWeight: 500 }}>{t.name}</span> },
    { key: 'value', header: 'Value', numeric: true, render: (t) => t.value },
    { key: 'source', header: 'Source', render: (t) => <span style={{ color: 'var(--fg-muted)' }}>{t.source ?? '—'}</span> },
  ];

  return (
    <Section index={10} title="Appendix" site={site} lead="Rubric, thresholds, glossary, and methodology detail.">
      <div className="flex flex-col gap-6">
        <Block title="Severity rubric">
          {appendix.rubric.length === 0 ? (
            <NoData label="No rubric supplied." />
          ) : (
            <div className="flex flex-col" style={{ border: '1px solid var(--hairline)', borderRadius: 'var(--radius-card)', overflow: 'hidden' }}>
              {appendix.rubric.map((r, i) => (
                <div
                  key={r.severity}
                  className="grid gap-3 px-4 py-3 items-baseline"
                  style={{ gridTemplateColumns: '110px 1fr', borderTop: i === 0 ? 'none' : '1px solid var(--hairline)' }}
                >
                  <SeverityChip severity={r.severity} />
                  <span className="t-secondary" style={{ color: 'var(--fg-muted)', maxWidth: '42rem' }}>
                    {r.definition}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Block>

        <Block title="Thresholds used">
          {appendix.thresholds.length === 0 ? (
            <NoData label="No thresholds recorded." />
          ) : (
            <DocTable columns={thresholdColumns} rows={appendix.thresholds} rowKey={(t) => t.name} />
          )}
        </Block>

        <Block title="Glossary">
          {appendix.glossary.length === 0 ? (
            <NoData label="No glossary supplied." />
          ) : (
            <dl className="grid gap-x-6 gap-y-2" style={{ gridTemplateColumns: 'auto 1fr' }}>
              {appendix.glossary.map((g) => (
                <div key={g.term} style={{ display: 'contents' }}>
                  <dt className="t-body" style={{ color: 'var(--fg)', fontWeight: 500 }}>
                    {g.term}
                  </dt>
                  <dd className="t-secondary" style={{ color: 'var(--fg-muted)', margin: 0, maxWidth: '40rem' }}>
                    {g.definition}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Block>

        {appendix.methodology_detail.length > 0 && (
          <Block title="Methodology detail">
            <ul className="flex flex-col gap-1.5" style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {appendix.methodology_detail.map((m, i) => (
                <li key={i} className="flex gap-2 t-secondary" style={{ color: 'var(--fg-muted)' }}>
                  <span aria-hidden style={{ color: 'var(--fg-subtle)' }}>
                    ·
                  </span>
                  <span style={{ maxWidth: '44rem' }}>{m}</span>
                </li>
              ))}
            </ul>
          </Block>
        )}
      </div>
    </Section>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="t-section mb-3" style={{ color: 'var(--fg)' }}>
        {title}
      </h3>
      {children}
    </div>
  );
}
